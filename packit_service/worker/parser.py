# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
Parser is transforming github JSONs into `events` objects
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, ClassVar, Optional, Union

from packit.config import JobConfigTriggerType
from packit.utils import nested_get

from packit_service.constants import (
    TESTING_FARM_INSTALLABILITY_TEST_URL,
)
from packit_service.events import (
    abstract,
    copr,
    forgejo,
    pagure,
    testing_farm,
)
from packit_service.events.enums import (
    IssueCommentAction,
    PullRequestAction,
    PullRequestCommentAction,
)
from packit_service.models import (
    ProjectEventModel,
    TestingFarmResult,
    TFTTestRunTargetModel,
)
from packit_service.worker.handlers.abstract import MAP_CHECK_PREFIX_TO_HANDLER
from packit_service.worker.helpers.build import CoprBuildJobHelper
from packit_service.worker.helpers.testing_farm import TestingFarmClient

logger = logging.getLogger(__name__)


class PackitParserException(Exception):
    pass


@dataclass
class _TestingFarmCommonData:
    project_url: str
    ref: str
    result: TestingFarmResult
    summary: str
    copr_build_id: str
    copr_chroot: str
    compose: str
    log_url: str
    created: datetime
    identifier: Optional[str]


class Parser:
    """
    Once we receive a new event (GitHub/GitLab webhook) for every event
    we need to have method inside the `Parser` class to create objects defined in `event.py`.
    """

    @staticmethod
    def parse_event(
        event: dict,
    ) -> Optional[
        Union[
            abstract.comment.Commit,
            copr.CoprBuild,
            testing_farm.Result,
            forgejo.pr.Action,
            forgejo.pr.Comment,
            forgejo.issue.Comment,
            forgejo.push.Commit,
        ]
    ]:
        """
        Try to parse all JSONs that we process.

        When reacting to fedmsg events, be aware that we are squashing the structure
        so we take only `body` with the `topic` key included.
        See: https://github.com/packit/packit-service-fedmsg/blob/
             e53586bf7ace0c46fd6812fe8dc11491e5e6cf41/packit_service_fedmsg/consumer.py#L137

        :param event: JSON from GitHub/GitLab
        :return: event object
        """

        if not event:
            logger.warning("No event to process!")
            return None

        for response in (
            parser(event)
            for parser in (
                Parser.parse_testing_farm_results_event,
                Parser.parse_copr_event,
                Parser.parse_forgejo_pr_event,
                Parser.parse_forgejo_push_event,
                Parser.parse_forgejo_comment_event,
            )
        ):
            if response:
                return response

        logger.debug("We don't process this event.")
        return None

    @staticmethod
    def parse_forgejo_push_event(event: dict) -> Optional[forgejo.push.Commit]:
        raw_ref = event.get("ref")
        before = event.get("before")
        after = event.get("after")
        pusher = nested_get(event, "pusher", "login") or nested_get(event, "pusher", "name")

        if not (raw_ref and after and before and pusher):
            return None

        # Forgejo sets `deleted` identically to GitHub
        if event.get("deleted"):
            logger.info(f"Forgejo push event on '{raw_ref}' by {pusher} to delete ref")
            return None

        # Number of commits introduced by this push
        commits = event.get("commits") or []
        num_commits = len(commits)

        # Strip the ref prefix to get the branch/tag name
        _, ref_type, ref_name = raw_ref.split("/", 2)
        if ref_type != "heads":
            logger.debug(f"Forgejo push event ignored – not a branch push ('{raw_ref}')")
            return None

        logger.info(
            f"Forgejo push event on '{ref_name}': "
            f"{before[:8]} → {after[:8]} by {pusher} "
            f"({num_commits} {'commit' if num_commits == 1 else 'commits'})"
        )

        repo_namespace = nested_get(event, "repository", "owner", "login")
        repo_name = nested_get(event, "repository", "name")
        repo_url = nested_get(event, "repository", "html_url")

        if not (repo_namespace and repo_name):
            logger.warning("Forgejo push event missing repository namespace/name")
            return None

        return forgejo.push.Commit(
            repo_namespace=repo_namespace,
            repo_name=repo_name,
            git_ref=ref_name,
            project_url=repo_url,
            commit_sha=after,
            commit_sha_before=before,
        )

    @staticmethod
    def parse_forgejo_pr_event(event) -> Optional[forgejo.pr.Action]:
        """
        Parse Forgejo PR action events, only triggering for relevant actions.
        Supported actions: 'opened', 'reopened', 'synchronize'.
        Skips others like 'closed'.
        """
        action_str = event.get("action")
        # Only trigger for these actions
        supported_actions = {"opened", "reopened", "synchronize"}
        if action_str == "synchronized":
            action_str = "synchronize"
            event["action"] = action_str
        if action_str not in supported_actions:
            logger.info(f"Skipping PR action: {action_str}")
            return None

        pr = event.get("pull_request")
        if not pr:
            logger.warning("No pull_request in event.")
            return None

        pr_id = pr.get("number")
        actor = event.get("sender", {}).get("login")
        repo = event.get("repository", {})
        base = pr.get("base")
        head = pr.get("head")
        body = pr.get("body")

        # Check all required nested fields
        try:
            base_repo_namespace = base["repo"]["owner"]["login"]
            base_repo_name = base["repo"]["name"]
            base_ref = base["ref"]
            target_repo_namespace = head["repo"]["owner"]["login"]
            target_repo_name = head["repo"]["name"]
            project_url = repo["html_url"]
            commit_sha = head["sha"]
        except (TypeError, KeyError):
            logger.warning("Missing required nested fields in PR event.")
            return None

        return forgejo.pr.Action(
            action=PullRequestAction[action_str],
            pr_id=pr_id,
            base_repo_namespace=base_repo_namespace,
            base_repo_name=base_repo_name,
            base_ref=base_ref,
            target_repo_namespace=target_repo_namespace,
            target_repo_name=target_repo_name,
            project_url=project_url,
            commit_sha=commit_sha,
            commit_sha_before=event.get("before", ""),  # Optional, might be empty
            actor=actor,
            body=body,
        )

    @staticmethod
    def parse_forgejo_comment_event(
        event: dict,
    ) -> Optional[Union[forgejo.pr.Comment, forgejo.issue.Comment]]:
        """Since Forgejo treats PR as special issues the comments are basically on issues,
        we need to distinguish between Forgejo issue and PR comments and parse accordingly."""

        issue_id = nested_get(event, "issue", "number")
        action = event.get("action")
        if action not in {"created", "edited"} or not issue_id:
            return None

        # Only treat as PR if 'pull_request' is present and not None
        issue_dict = event.get("issue", {})
        is_pr = "pull_request" in issue_dict and issue_dict["pull_request"] is not None

        comment = nested_get(event, "comment", "body")
        comment_id = nested_get(event, "comment", "id")
        logger.info(
            f"Forgejo {'PR' if is_pr else 'issue'}#{issue_id} "
            f"comment: {comment!r} id#{comment_id} {action!r} event."
        )

        base_repo_namespace = nested_get(event, "issue", "user", "login")
        base_repo_name = nested_get(event, "repository", "name")

        user_login = nested_get(event, "comment", "user", "login")
        target_repo_namespace = nested_get(event, "repository", "owner", "login")

        target_repo_name = nested_get(event, "repository", "name")
        https_url = nested_get(event, "repository", "html_url")

        if not (
            base_repo_name and base_repo_namespace and target_repo_name and target_repo_namespace
        ):
            logger.warning("Missing repo info in Forgejo event.")
            return None

        if not user_login:
            logger.warning("No user login in comment.")
            return None

        if is_pr:
            return forgejo.pr.Comment(
                action=PullRequestCommentAction[action],
                pr_id=issue_id,
                base_ref="",
                base_repo_namespace=base_repo_namespace,
                base_repo_name=base_repo_name,
                target_repo_namespace=target_repo_namespace,
                target_repo_name=target_repo_name,
                project_url=https_url,
                actor=user_login,
                comment=comment,
                comment_id=comment_id,
                commit_sha=None,
            )
        return forgejo.issue.Comment(
            action=IssueCommentAction[action],
            issue_id=issue_id,
            repo_namespace=base_repo_namespace,
            repo_name=base_repo_name,
            target_repo=f"{target_repo_namespace}/{target_repo_name}",
            project_url=https_url,
            actor=user_login,
            comment=comment,
            comment_id=comment_id,
            tag_name="",
            base_ref="",
            dist_git_project_url=None,
        )

    @staticmethod
    def parse_check_name(
        check_name: str,
        db_project_event: ProjectEventModel,
    ) -> Optional[tuple[str, str, str]]:
        """
        Parse the given name of the check run.

        Check name examples:
        "rpm-build:fedora-34-x86_64"
        "rpm-build:fedora-34-x86_64:identifier"
        "rpm-build:main:fedora-34-x86_64:identifier"
        "propose-downstream:f35"

        For the build and test runs, if the project event is release/commit, the branch
        name or release name is included in the check name - it can be ignored,
        since we are having the DB project event (obtained via external ID of the check).

        Returns:
            tuple of job name (e.g. rpm-build), target and identifier obtained from check run
            (or None if the name cannot be parsed)
        """
        check_name_parts = check_name.split(":", maxsplit=3)
        if len(check_name_parts) < 1:
            logger.warning(f"{check_name} cannot be parsed")
            return None
        check_name_job = check_name_parts[0]

        if check_name_job not in MAP_CHECK_PREFIX_TO_HANDLER:
            logger.warning(
                f"{check_name_job} not in {list(MAP_CHECK_PREFIX_TO_HANDLER.keys())}",
            )
            return None

        check_name_target, check_name_identifier = None, None
        db_project_object = db_project_event.get_project_event_object()

        if len(check_name_parts) == 2:
            _, check_name_target = check_name_parts
        elif len(check_name_parts) == 3:
            build_test_job_names = (
                CoprBuildJobHelper.status_name_build,
                CoprBuildJobHelper.status_name_test,
            )
            if (
                check_name_job in build_test_job_names
                and db_project_object.job_config_trigger_type
                in (
                    JobConfigTriggerType.commit,
                    JobConfigTriggerType.release,
                )
            ):
                (
                    _,
                    _,
                    check_name_target,
                ) = check_name_parts
            else:
                (
                    _,
                    check_name_target,
                    check_name_identifier,
                ) = check_name_parts
        elif len(check_name_parts) == 4:
            (
                _,
                _,
                check_name_target,
                check_name_identifier,
            ) = check_name_parts
        else:
            logger.warning(f"{check_name_job} cannot be parsed")
            check_name_job = None

        if not (check_name_job and check_name_target):
            logger.warning(
                f"We were not able to parse the job and target "
                f"from the check run name {check_name}.",
            )
            return None

        logger.info(
            f"Check name job: {check_name_job}, check name target: {check_name_target}, "
            f"check name identifier: {check_name_identifier}",
        )

        return check_name_job, check_name_target, check_name_identifier

    @staticmethod
    def parse_data_from_testing_farm(
        tft_test_run: TFTTestRunTargetModel,
        event: dict[Any, Any],
    ) -> _TestingFarmCommonData:
        """Parses common data from testing farm response.

        Such common data is environment, os, summary and others.

        Args:
            tft_test_run (TFTTestRunTargetModel): Entry of the related test run in DB.
            event (dict): Response from testing farm converted to a dict.

        Returns:
            An instance of `_TestingFarmCommonData` data class.
        """
        tf_state = event.get("state")
        tf_result = nested_get(event, "result", "overall")

        logger.debug(f"TF payload: state = {tf_state}, result['overall'] = {tf_result}")

        # error and complete are the end states
        if tf_state not in ("complete", "error"):
            result = TestingFarmResult.from_string(tf_state or "unknown")
        else:
            result = TestingFarmResult.from_string(tf_result or tf_state or "unknown")

        summary: str = nested_get(event, "result", "summary") or ""
        env: dict = nested_get(event, "environments_requested", 0, default={})
        compose: str = nested_get(env, "os", "compose")
        created: str = event.get("created")
        identifier: Optional[str] = None
        created_dt: Optional[datetime] = None
        if created:
            created_dt = datetime.fromisoformat(created)
            created_dt = created_dt.replace(tzinfo=timezone.utc)

        ref: str = nested_get(event, "test", "fmf", "ref")
        fmf_url: str = nested_get(event, "test", "fmf", "url")

        # ["test"]["fmf"]["ref"] contains ref to the TF test, i.e. "master",
        # but we need the original commit_sha to be able to continue
        if tft_test_run:
            ref = tft_test_run.commit_sha
            identifier = tft_test_run.identifier

        if fmf_url == TESTING_FARM_INSTALLABILITY_TEST_URL:
            # There are no artifacts in install-test results
            copr_build_id = copr_chroot = ""
            summary = {
                TestingFarmResult.passed: "Installation passed",
                TestingFarmResult.failed: "Installation failed",
            }.get(result, summary)
        else:
            artifact: dict = nested_get(env, "artifacts", 0, default={})
            a_type: str = artifact.get("type")
            if a_type == "fedora-copr-build":
                copr_build_id = artifact["id"].split(":")[0]
                copr_chroot = artifact["id"].split(":")[1]
            else:
                logger.debug(f"{a_type} != fedora-copr-build")
                copr_build_id = copr_chroot = ""

        if not copr_chroot and tft_test_run:
            copr_chroot = tft_test_run.target

        # ["test"]["fmf"]["url"] contains PR's source/fork url or TF's install test url.
        # We need the original/base project url stored in db.
        if tft_test_run and tft_test_run.data and "base_project_url" in tft_test_run.data:
            project_url = tft_test_run.data["base_project_url"]
        else:
            project_url = fmf_url if fmf_url != TESTING_FARM_INSTALLABILITY_TEST_URL else None

        log_url: str = nested_get(event, "run", "artifacts")

        return _TestingFarmCommonData(
            project_url=project_url,
            ref=ref,
            result=result,
            summary=summary,
            copr_build_id=copr_build_id,
            copr_chroot=copr_chroot,
            compose=compose,
            log_url=log_url,
            created=created_dt,
            identifier=identifier,
        )

    @staticmethod
    def parse_testing_farm_results_event(
        event: dict,
    ) -> Optional[testing_farm.Result]:
        """this corresponds to testing farm results event"""
        if event.get("source") != "testing-farm" or not event.get("request_id"):
            return None

        request_id: str = event["request_id"]
        logger.info(f"Testing farm notification event. Request ID: {request_id}")

        tft_test_run = TFTTestRunTargetModel.get_by_pipeline_id(request_id)

        # Testing Farm sends only request/pipeline id in a notification.
        # We need to get more details ourselves.
        # It'd be much better to do this in TestingFarmResultsHandler.run(),
        # but all the code along the way to get there expects we already know the details.
        # TODO: Get missing info from db instead of querying TF
        event = TestingFarmClient.get_request_details(request_id)
        if not event:
            # Something's wrong with TF, raise exception so that we can re-try later.
            raise Exception(f"Failed to get {request_id} details from TF.")

        data = Parser.parse_data_from_testing_farm(tft_test_run, event)

        logger.debug(
            f"project_url: {data.project_url}, ref: {data.ref}, result: {data.result}, "
            f"summary: {data.summary!r}, copr-build: {data.copr_build_id}:{data.copr_chroot},\n"
            f"log_url: {data.log_url}",
        )

        return testing_farm.Result(
            pipeline_id=request_id,
            result=data.result,
            compose=data.compose,
            summary=data.summary,
            log_url=data.log_url,
            copr_build_id=data.copr_build_id,
            copr_chroot=data.copr_chroot,
            commit_sha=data.ref,
            project_url=data.project_url,
            created=data.created,
            identifier=data.identifier,
        )

    @staticmethod
    def parse_copr_event(event) -> Optional[copr.CoprBuild]:
        """this corresponds to copr build event e.g:"""
        topic = event.get("topic")

        copr_build_cls: type[copr.CoprBuild]
        if topic == "org.fedoraproject.prod.copr.build.start":
            copr_build_cls = copr.Start
        elif topic == "org.fedoraproject.prod.copr.build.end":
            copr_build_cls = copr.End
        else:
            # Topic not supported.
            return None

        logger.info(f"Copr event; {event.get('what')}")

        build_id = event.get("build")
        chroot = event.get("chroot")
        status = event.get("status")
        owner = event.get("owner")
        project_name = event.get("copr")
        pkg = event.get("pkg")
        timestamp = event.get("timestamp")

        return copr_build_cls.from_build_id(
            topic,
            build_id,
            chroot,
            status,
            owner,
            project_name,
            pkg,
            timestamp,
        )

    @staticmethod
    def parse_pagure_pr_flag_event(event) -> Optional[pagure.pr.Flag]:
        """
        Look into the provided event and see if it is Pagure PR Flag added/updated event.
        https://fedora-fedmsg.readthedocs.io/en/latest/topics.html#pagure-pull-request-flag-added
        https://fedora-fedmsg.readthedocs.io/en/latest/topics.html#pagure-pull-request-flag-updated
        """

        if ".pagure.pull-request.flag." not in (topic := event.get("topic", "")):
            return None
        logger.info(f"Pagure PR flag event, topic: {topic}")

        if (flag := event.get("flag")) is None:
            return None
        username = flag.get("username")
        comment = flag.get("comment")
        status = flag.get("status")
        date_updated = int(d) if (d := flag.get("date_updated")) else None
        url = flag.get("url")
        commit_sha = flag.get("commit_hash")

        pr_id: int = nested_get(event, "pullrequest", "id")
        pr_url = nested_get(event, "pullrequest", "full_url")
        pr_source_branch = nested_get(event, "pullrequest", "branch_from")

        project_url = nested_get(event, "pullrequest", "project", "full_url")
        project_name = nested_get(event, "pullrequest", "project", "name")
        project_namespace = nested_get(event, "pullrequest", "project", "namespace")

        return pagure.pr.Flag(
            username=username,
            comment=comment,
            status=status,
            date_updated=date_updated,
            url=url,
            commit_sha=commit_sha,
            pr_id=pr_id,
            pr_url=pr_url,
            pr_source_branch=pr_source_branch,
            project_url=project_url,
            project_name=project_name,
            project_namespace=project_namespace,
        )

    @staticmethod
    def parse_pagure_pull_request_comment_event(
        event,
    ) -> Optional[pagure.pr.Comment]:
        if ".pagure.pull-request.comment." not in (topic := event.get("topic", "")):
            return None
        logger.info(f"Pagure PR comment event, topic: {topic}")

        action = PullRequestCommentAction.created.value
        pr_id = event["pullrequest"]["id"]
        pagure_login = event["agent"]
        if pagure_login in {"packit", "packit-stg"}:
            logger.debug("Our own comment.")
            return None

        base_repo_namespace = event["pullrequest"]["project"]["namespace"]
        base_repo_name = event["pullrequest"]["project"]["name"]
        repo_from = event["pullrequest"]["repo_from"]
        base_repo_owner = repo_from["user"]["name"] if repo_from else pagure_login
        target_repo = repo_from["name"] if repo_from else base_repo_name
        https_url = event["pullrequest"]["project"]["full_url"]
        source_project_url = repo_from["full_url"] if repo_from else https_url
        commit_sha = event["pullrequest"]["commit_stop"]

        if "added" in event["topic"]:
            comment = event["pullrequest"]["comments"][-1]["comment"]
            comment_id = event["pullrequest"]["comments"][-1]["id"]
        else:
            raise ValueError(
                f"Unknown comment location in response for {event['topic']}",
            )

        return pagure.pr.Comment(
            action=PullRequestCommentAction[action],
            pr_id=pr_id,
            base_repo_namespace=base_repo_namespace,
            base_repo_name=base_repo_name,
            base_repo_owner=base_repo_owner,
            base_ref=None,
            target_repo=target_repo,
            project_url=https_url,
            source_project_url=source_project_url,
            commit_sha=commit_sha,
            user_login=pagure_login,
            comment=comment,
            comment_id=comment_id,
        )

    @staticmethod
    def parse_pagure_pull_request_event(
        event,
    ) -> Optional[pagure.pr.Action]:
        if (topic := event.get("topic", "")) not in (
            "org.fedoraproject.prod.pagure.pull-request.new",
            "org.fedoraproject.prod.pagure.pull-request.updated",
            "org.fedoraproject.prod.pagure.pull-request.rebased",
        ):
            return None

        logger.info(f"Pagure PR event, topic: {topic}")

        action = (
            PullRequestAction.opened.value
            if topic.endswith("new")
            else PullRequestAction.synchronize.value
        )
        pr_id = event["pullrequest"]["id"]
        pagure_login = event["agent"]

        base_repo_namespace = event["pullrequest"]["project"]["namespace"]
        base_repo_name = event["pullrequest"]["project"]["name"]
        repo_from = event["pullrequest"]["repo_from"]
        base_repo_owner = repo_from["user"]["name"] if repo_from else pagure_login
        target_repo = repo_from["name"] if repo_from else base_repo_name
        https_url = event["pullrequest"]["project"]["full_url"]
        source_project_url = repo_from["full_url"] if repo_from else https_url
        commit_sha = event["pullrequest"]["commit_stop"]
        target_branch = event["pullrequest"]["branch"]

        return pagure.pr.Action(
            action=PullRequestAction[action],
            pr_id=pr_id,
            base_repo_namespace=base_repo_namespace,
            base_repo_name=base_repo_name,
            base_repo_owner=base_repo_owner,
            base_ref=None,
            target_repo=target_repo,
            project_url=https_url,
            source_project_url=source_project_url,
            commit_sha=commit_sha,
            user_login=pagure_login,
            target_branch=target_branch,
        )

    # The .__func__ are needed for Python < 3.10
    MAPPING: ClassVar[dict[str, dict[str, Callable]]] = {
        "forgejo": {
            "push": parse_forgejo_push_event.__func__,  # type: ignore
            "pull_request": parse_forgejo_pr_event.__func__,  # type: ignore
            "issue_comment": parse_forgejo_comment_event.__func__,  # type: ignore
        },
        "fedora-messaging": {
            "pagure.pull-request.flag.added": parse_pagure_pr_flag_event.__func__,  # type: ignore
            "pagure.pull-request.flag.updated": parse_pagure_pr_flag_event.__func__,  # type: ignore
            "pagure.pull-request.comment.added": parse_pagure_pull_request_comment_event.__func__,  # type: ignore
            "pagure.pull-request.new": parse_pagure_pull_request_event.__func__,  # type: ignore
            "pagure.pull-request.updated": parse_pagure_pull_request_event.__func__,  # type: ignore
            "pagure.pull-request.rebased": parse_pagure_pull_request_event.__func__,  # type: ignore
            "copr.build.start": parse_copr_event.__func__,  # type: ignore
            "copr.build.end": parse_copr_event.__func__,  # type: ignore
        },
        "testing-farm": {
            "results": parse_testing_farm_results_event.__func__,  # type: ignore
        },
    }
