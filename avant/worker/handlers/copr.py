# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

import logging
import os
from datetime import datetime, timezone
from typing import Optional

import requests
import json
from celery import Task, signature
from packit.config import (
    JobConfig,
    JobConfigTriggerType,
    JobType,
)
from packit.config.package_config import PackageConfig
from packit.constants import HTTP_REQUEST_TIMEOUT
from packit_service.service.urls import get_copr_build_info_url, get_srpm_build_info_url

from packit_service.constants import (
    COPR_API_SUCC_STATE,
    COPR_SRPM_CHROOT,
)
from avant.events import abstract, copr, forgejo
from packit_service.models import (
    BuildStatus,
    CoprBuildTargetModel,
)
from avant.worker.utils import (
    dump_job_config,
    dump_package_config,
    elapsed_seconds,
)
from avant.worker.checker.abstract import Checker
from avant.worker.checker.copr import (
    AreOwnerAndProjectMatchingJob,
    BuildNotAlreadyStarted,
    CanActorRunTestsJob,
    IsGitForgeProjectAndEventOk,
    IsJobConfigTriggerMatching,
    IsPackageMatchingJobView,
)
from avant.worker.handlers.abstract import (
    JobHandler,
    RetriableJobHandler,
    AvantTaskName,
    configured_as,
    reacts_to,
    run_for_comment,
)
from avant.worker.handlers.mixin import (
    ConfigFromEventMixin,
    GetCoprBuildEventMixin,
    GetCoprBuildJobHelperForIdMixin,
    GetCoprBuildJobHelperMixin,
)
from avant.worker.mixin import PackitAPIWithDownstreamMixin
from avant.worker.reporting import BaseCommitStatus, DuplicateCheckMode
from avant.worker.result import TaskResults
from ogr.services.forgejo import ForgejoProject
from ogr.services.github import GithubProject
from ogr.services.gitlab import GitlabProject

logger = logging.getLogger(__name__)


@configured_as(job_type=JobType.copr_build)
@run_for_comment(command="build")
@reacts_to(forgejo.pr.Comment)
@reacts_to(forgejo.pr.Action)
@reacts_to(abstract.comment.Commit)
class CoprBuildHandler(
    RetriableJobHandler,
    ConfigFromEventMixin,
    PackitAPIWithDownstreamMixin,
    GetCoprBuildJobHelperMixin,
):
    task_name = AvantTaskName.copr_build

    def __init__(
        self,
        package_config: PackageConfig,
        job_config: JobConfig,
        event: dict,
        celery_task: Task,
        copr_build_group_id: Optional[int] = None,
    ):
        super().__init__(
            package_config=package_config,
            job_config=job_config,
            event=event,
            celery_task=celery_task,
        )
        self._copr_build_group_id = copr_build_group_id

    @staticmethod
    def get_checkers() -> tuple[type[Checker], ...]:
        return (
            IsJobConfigTriggerMatching,
            IsGitForgeProjectAndEventOk,
            CanActorRunTestsJob,
        )

    def run(self) -> TaskResults:
        # [XXX] For now cancel only when an environment variable is defined,
        # should allow for less stressful testing and also optionally turning
        # the cancelling on-and-off on the prod
        if os.getenv("CANCEL_RUNNING_JOBS"):
            self.copr_build_helper.cancel_running_builds()

        return self.copr_build_helper.run_copr_build_from_source_script()


class AbstractCoprBuildReportHandler(
    JobHandler,
    PackitAPIWithDownstreamMixin,
    GetCoprBuildJobHelperForIdMixin,
    GetCoprBuildEventMixin,
):
    @staticmethod
    def get_checkers() -> tuple[type[Checker], ...]:
        return (AreOwnerAndProjectMatchingJob, IsPackageMatchingJobView)


@configured_as(job_type=JobType.copr_build)
@reacts_to(event=copr.Start)
class CoprBuildStartHandler(AbstractCoprBuildReportHandler):
    topic = "org.fedoraproject.prod.copr.build.start"
    task_name = AvantTaskName.copr_build_start

    @staticmethod
    def get_checkers() -> tuple[type[Checker], ...]:
        return (
            *super(CoprBuildStartHandler, CoprBuildStartHandler).get_checkers(),
            BuildNotAlreadyStarted,
        )

    def set_start_time(self):
        start_time = (
            datetime.utcfromtimestamp(self.copr_event.timestamp)
            if self.copr_event.timestamp
            else None
        )
        self.build.set_start_time(start_time)

    def set_logs_url(self):
        copr_build_logs = self.copr_event.get_copr_build_logs_url()
        self.build.set_build_logs_url(copr_build_logs)

    def run(self):
        if not self.build:
            model = "SRPMBuildDB" if self.copr_event.chroot == COPR_SRPM_CHROOT else "CoprBuildDB"
            msg = f"Copr build {self.copr_event.build_id} not in {model}."
            logger.warning(msg)
            return TaskResults(success=False, details={"msg": msg})

        if self.build.build_start_time is not None:
            msg = f"Copr build start for {self.copr_event.build_id} is already processed."
            logger.debug(msg)
            return TaskResults(success=True, details={"msg": msg})

        if BuildStatus.is_final_state(self.build.status):
            msg = (
                "Copr build start is being processed, but the DB build "
                "is already in the final state, setting only start time."
            )
            logger.debug(msg)
            self.set_start_time()
            return TaskResults(success=True, details={"msg": msg})

        self.set_logs_url()

        if self.copr_event.chroot == COPR_SRPM_CHROOT:
            url = get_srpm_build_info_url(self.build.id)
            report_status = (
                self.copr_build_helper.report_status_to_all
                if self.job_config.sync_test_job_statuses_with_builds
                else self.copr_build_helper.report_status_to_build
            )
            report_status(
                description="SRPM build is in progress...",
                state=BaseCommitStatus.running,
                url=url,
            )
            msg = "SRPM build in Copr has started..."
            self.set_start_time()
            return TaskResults(success=True, details={"msg": msg})

        url = get_copr_build_info_url(self.build.id)
        self.build.set_status(BuildStatus.pending)

        report_status_for_chroot = (
            self.copr_build_helper.report_status_to_all_for_chroot
            if self.job_config.sync_test_job_statuses_with_builds
            else self.copr_build_helper.report_status_to_build_for_chroot
        )
        report_status_for_chroot(
            description="RPM build is in progress...",
            state=BaseCommitStatus.running,
            url=url,
            chroot=self.copr_event.chroot,
        )
        msg = f"Build on {self.copr_event.chroot} in copr has started..."
        self.set_start_time()
        return TaskResults(success=True, details={"msg": msg})


@configured_as(job_type=JobType.copr_build)
@reacts_to(event=copr.End)
class CoprBuildEndHandler(AbstractCoprBuildReportHandler):
    topic = "org.fedoraproject.prod.copr.build.end"
    task_name = AvantTaskName.copr_build_end

    def set_srpm_url(self) -> None:
        # TODO how to do better
        srpm_build = (
            self.build
            if self.copr_event.chroot == COPR_SRPM_CHROOT
            else self.build.get_srpm_build()
        )

        if srpm_build.url is not None:
            # URL has been already set
            return

        srpm_url = self.copr_build_helper.get_build(
            self.copr_event.build_id,
        ).source_package.get("url")

        if srpm_url is not None:
            srpm_build.set_url(srpm_url)

    def set_end_time(self):
        end_time = (
            datetime.utcfromtimestamp(self.copr_event.timestamp)
            if self.copr_event.timestamp
            else None
        )
        self.build.set_end_time(end_time)

    def measure_time_after_reporting(self):
        reported_time = datetime.now(timezone.utc)
        build_ended_on = self.copr_build_helper.get_build_chroot(
            int(self.build.build_id),
            self.build.target,
        ).ended_on

        reported_after_time = elapsed_seconds(
            begin=datetime.fromtimestamp(build_ended_on, timezone.utc),
            end=reported_time,
        )
        logger.debug(
            f"Copr build end reported after {reported_after_time / 60} minutes.",
        )

    def set_built_packages(self):
        if self.build.built_packages:
            # packages have been already set
            return

        built_packages = self.copr_build_helper.get_built_packages(
            int(self.build.build_id),
            self.build.target,
        )
        self.build.set_built_packages(built_packages)

    def run(self):
        if not self.build:
            # TODO: how could this happen?
            model = "SRPMBuildDB" if self.copr_event.chroot == COPR_SRPM_CHROOT else "CoprBuildDB"
            msg = f"Copr build {self.copr_event.build_id} not in {model}."
            logger.warning(msg)
            return TaskResults(success=False, details={"msg": msg})

        if self.build.status in [
            BuildStatus.success,
            BuildStatus.failure,
        ]:
            msg = (
                f"Copr build {self.copr_event.build_id} is already"
                f" processed (status={self.copr_event.build.status})."
            )
            logger.info(msg)
            return TaskResults(success=True, details={"msg": msg})

        self.set_end_time()
        self.set_srpm_url()

        if self.copr_event.chroot == COPR_SRPM_CHROOT:
            return self.handle_srpm_end()


        # if the build is needed only for test, it doesn't have the task_accepted_time
        if self.build.task_accepted_time:
            copr_build_time = elapsed_seconds(
                begin=self.build.task_accepted_time,
                end=datetime.now(timezone.utc),
            )
            logger.info(f"Copr build finished time: {copr_build_time}")
            
        # https://pagure.io/copr/copr/blob/master/f/common/copr_common/enums.py#_42
        if self.copr_event.status != COPR_API_SUCC_STATE:
            failed_msg = "RPMs failed to be built."
            packit_dashboard_url = get_copr_build_info_url(self.build.id)
            # if SRPM build failed it has been reported already so skip reporting
            if self.build.get_srpm_build().status != BuildStatus.failure:
                self.copr_build_helper.report_status_to_all_for_chroot(
                    state=BaseCommitStatus.failure,
                    description=failed_msg,
                    url=packit_dashboard_url,
                    chroot=self.copr_event.chroot,
                )
                self.measure_time_after_reporting()
                self.copr_build_helper.notify_about_failure_if_configured(
                    packit_dashboard_url=packit_dashboard_url,
                    external_dashboard_url=self.build.web_url,
                    logs_url=self.build.build_logs_url,
                )
            self.build.set_status(BuildStatus.failure)
            return TaskResults(success=False, details={"msg": failed_msg})

        self.report_successful_build()
        self.measure_time_after_reporting()

        self.set_built_packages()
        self.build.set_status(BuildStatus.success)
        self.handle_fedora_review()
        self.handle_testing_farm()

        return TaskResults(success=True, details={})

    def report_successful_build(self):
        if (
            self.copr_build_helper.job_build
            and self.copr_build_helper.job_build.trigger == JobConfigTriggerType.pull_request
            and self.copr_event.pr_id
            and isinstance(self.project, (GithubProject, GitlabProject, ForgejoProject))
            and self.job_config.notifications.pull_request.successful_build
        ):
            msg = (
                f"Congratulations! One of the builds has completed. :champagne:\n\n"
                "You can install the built RPMs by following these steps:\n\n"
                "* `sudo yum install -y dnf-plugins-core` on RHEL 8\n"
                "* `sudo dnf install -y dnf-plugins-core` on Fedora\n"
                f"* `dnf copr enable {self.copr_event.owner}/{self.copr_event.project_name}`\n"
                "* And now you can install the packages.\n"
                "\nPlease note that the RPMs should be used only in a testing environment."
            )
            self.copr_build_helper.status_reporter.comment(
                msg,
                duplicate_check=DuplicateCheckMode.check_last_comment,
            )

        url = get_copr_build_info_url(self.build.id)

        self.copr_build_helper.report_status_to_build_for_chroot(
            state=BaseCommitStatus.success,
            description="RPMs were built successfully.",
            url=url,
            chroot=self.copr_event.chroot,
        )
        if self.job_config.sync_test_job_statuses_with_builds:
            self.copr_build_helper.report_status_to_all_test_jobs_for_chroot(
                state=BaseCommitStatus.pending,
                description="RPMs were built successfully.",
                url=url,
                chroot=self.copr_event.chroot,
            )

    def handle_srpm_end(self):
        url = get_srpm_build_info_url(self.build.id)

        if self.copr_event.status != COPR_API_SUCC_STATE:
            failed_msg = "SRPM build failed, check the logs for details."
            self.copr_build_helper.report_status_to_all(
                state=BaseCommitStatus.failure,
                description=failed_msg,
                url=url,
            )
            self.copr_build_helper.notify_about_failure_if_configured(
                packit_dashboard_url=url,
                external_dashboard_url=self.build.copr_web_url,
                logs_url=self.build.logs_url,
            )
            self.build.set_status(BuildStatus.failure)
            self.copr_build_helper.monitor_not_submitted_copr_builds(
                len(self.copr_build_helper.build_targets),
                "srpm_failure",
            )
            return TaskResults(success=False, details={"msg": failed_msg})

        for build in CoprBuildTargetModel.get_all_by_build_id(
            str(self.copr_event.build_id),
        ):
            # from waiting_for_srpm to pending
            build.set_status(BuildStatus.pending)

        self.build.set_status(BuildStatus.success)
        report_status = (
            self.copr_build_helper.report_status_to_all
            if self.job_config.sync_test_job_statuses_with_builds
            else self.copr_build_helper.report_status_to_build
        )
        report_status(
            state=BaseCommitStatus.running,
            description="SRPM build succeeded. Waiting for RPM build to start...",
            url=url,
        )
        msg = "SRPM build in Copr has finished."
        logger.debug(msg)
        return TaskResults(success=True, details={"msg": msg})

    def handle_fedora_review(self):
        """
        Handle fedora-review by fetching the review content and posting it as a formatted comment.

        The URL follows the pattern:
        https://download.copr.fedorainfracloud.org/results/{owner}/{project}/{chroot}/{build_id:08d}-{pkg}/fedora-review/review.txt
        """
        trigger = (
            self.copr_build_helper.job_build.trigger if self.copr_build_helper.job_build else None
        )
        logger.debug(f"trigger: {trigger}")
        logger.debug(f"pr_id: {self.copr_event.pr_id}")
        logger.debug(f"project type: {type(self.project)}")

        if (
            # Only post fedora-review for pull requests
            self.copr_build_helper.job_build
            and self.copr_build_helper.job_build.trigger == JobConfigTriggerType.pull_request
            and self.copr_event.pr_id
            and isinstance(self.project, (GithubProject, GitlabProject, ForgejoProject))
        ):
            logger.debug("All conditions met for fedora-review comment")
            # Construct the fedora-review URL based on the pattern
            review_url = (
                f"https://download.copr.fedorainfracloud.org/results/"
                f"{self.copr_event.owner}/{self.copr_event.project_name}/"
                f"{self.copr_event.chroot}/"
                f"{self.copr_event.build_id:08d}-{self.copr_event.pkg}/"
                f"fedora-review/review.json"
            )
            # we fetch the json file and parse it in a neat manner to post.
            try:
                logger.debug(f"Fetching fedora-review content from: {review_url}")
                # Fetch the review content
                response = requests.get(review_url, timeout=HTTP_REQUEST_TIMEOUT)
                response.raise_for_status()

                # Prefer JSON; fall back to plain text rendering
                content_type = response.headers.get("content-type", "").lower()
                parsed_json = None
                review_content = None
                try:
                    if "application/json" in content_type or response.text.strip().startswith("{"):
                        parsed_json = response.json()
                    else:
                        # still try JSON first; if it fails we'll treat as text
                        parsed_json = response.json()
                except Exception:
                    # Treat as text
                    try:
                        review_content = response.text.strip()
                    except UnicodeDecodeError as e:
                        logger.warning(f"Failed to decode review content as text: {e}")
                        raise requests.RequestException(f"Invalid text content: {e}") from e
                    if not review_content:
                        logger.warning(f"Empty review content fetched from {review_url}")
                        review_content = "No review content available."

                # Start message: minimal and unobtrusive
                msg = (
                    f"Fedora review completed for {self.copr_event.pkg} on {self.copr_event.chroot}.\n\n"
                )

                if parsed_json is not None:
                    # Summarize results
                    def summarize_results(data: dict) -> tuple[str, list[dict], dict]:
                        total_pass = total_pending = total_fail = 0
                        failed_items: list[dict] = []
                        by_severity = {"MUST": {"pass": 0, "pending": 0, "fail": 0},
                                       "SHOULD": {"pass": 0, "pending": 0, "fail": 0},
                                       "EXTRA": {"pass": 0, "pending": 0, "fail": 0}}

                        # top-level issues
                        for issue in data.get("issues", []) or []:
                            res = (issue or {}).get("result")
                            if res == "pass":
                                total_pass += 1
                            elif res == "pending":
                                total_pending += 1
                            elif res == "fail":
                                total_fail += 1
                                failed_items.append(issue)

                        # nested results
                        results = data.get("results", {}) or {}
                        for severity, sev_payload in results.items():
                            if not isinstance(sev_payload, dict):
                                continue
                            for group, items in sev_payload.items():
                                if not isinstance(items, list):
                                    continue
                                for it in items:
                                    res = (it or {}).get("result")
                                    if res == "pass":
                                        total_pass += 1
                                        if severity in by_severity:
                                            by_severity[severity]["pass"] += 1
                                    elif res == "pending":
                                        total_pending += 1
                                        if severity in by_severity:
                                            by_severity[severity]["pending"] += 1
                                    elif res == "fail":
                                        total_fail += 1
                                        if severity in by_severity:
                                            by_severity[severity]["fail"] += 1
                                        # enrich with context
                                        failed_items.append({
                                            "severity": severity,
                                            "group": group,
                                            "name": it.get("name"),
                                            "text": it.get("text"),
                                            "note": it.get("note"),
                                            "url": it.get("url"),
                                        })

                        summary_line = (
                            f"Summary: {total_fail} fail, {total_pending} pending, {total_pass} pass."
                        )
                        return summary_line, failed_items, by_severity

                    summary_line, failed_items, by_severity = summarize_results(parsed_json)

                    # Brief insights (non-obtrusive): key fails and section counts
                    insights: list[str] = []
                    # Highlight specific common failure if present
                    for fi in failed_items:
                        if fi.get("name") == "CheckNoNameConflict" or (
                            (fi.get("text") or "").lower().startswith("package does not use a name that already exists")
                        ):
                            link = fi.get("url") or ""
                            insight = "Name conflict detected (package name already exists)"
                            if link:
                                insight += f" — see {link}"
                            insights.append(insight)
                            break

                    # Per-section counts
                    for sev in ("MUST", "SHOULD", "EXTRA"):
                        counts = by_severity.get(sev, {})
                        if counts:
                            insights.append(
                                f"{sev}: {counts.get('fail', 0)} fail, {counts.get('pending', 0)} pending, {counts.get('pass', 0)} pass"
                            )

                    if insights:
                        msg += "\n".join(f"- {line}" for line in insights) + "\n\n"

                    # Minimal COPR instructions inline
                    msg += (
                        f"Install from COPR: `dnf copr enable {self.copr_event.owner}/{self.copr_event.project_name}; "
                        f"sudo dnf install {self.copr_event.pkg}`\n\n"
                    )

                    # Failed checks list (limit to 10)
                    fail_lines = []
                    for idx, fi in enumerate(failed_items[:10], start=1):
                        name = fi.get("name") or fi.get("text") or "Failed check"
                        text = fi.get("text") or ""
                        url = fi.get("url")
                        ctx = []
                        if fi.get("severity"):
                            ctx.append(fi.get("severity"))
                        if fi.get("group"):
                            ctx.append(fi.get("group"))
                        ctx_str = f" ({' / '.join(ctx)})" if ctx else ""
                        if url:
                            fail_lines.append(f"{idx}. [{name}]({url}){ctx_str} — {text}")
                        else:
                            fail_lines.append(f"{idx}. {name}{ctx_str} — {text}")

                    if failed_items:
                        msg += f"**{summary_line}**\n\n"
                        msg += "<details>\n<summary><strong>Failed checks</strong> (" \
                               f"{len(failed_items)})</summary>\n\n"
                        msg += "\n".join(fail_lines)
                        if len(failed_items) > 10:
                            msg += f"\n\n… and {len(failed_items) - 10} more."
                        msg += "\n\n</details>\n\n"
                    else:
                        msg += f"**{summary_line}**\n\n"

                    # Full parsed details (human-readable) inside collapsible
                    def render_parsed_details(data: dict) -> str:
                        lines: list[str] = []
                        # Top-level issues
                        issues = data.get("issues") or []
                        if issues:
                            lines.append("Issues:")
                            for it in issues:
                                name = (it or {}).get("name") or "Issue"
                                res = (it or {}).get("result") or ""
                                txt = (it or {}).get("text") or ""
                                note = (it or {}).get("note")
                                url = (it or {}).get("url")
                                line = f"- [{res}] {name}: {txt}"
                                if note:
                                    line += f" | Note: {note}"
                                if url:
                                    line += f" | Ref: {url}"
                                lines.append(line)
                            lines.append("")

                        # Detailed results by severity and group
                        results = data.get("results", {}) or {}
                        for severity in ("MUST", "SHOULD", "EXTRA"):
                            sev_payload = results.get(severity)
                            if not isinstance(sev_payload, dict):
                                continue
                            lines.append(f"{severity}:")
                            for group, items in sev_payload.items():
                                if not isinstance(items, list) or not items:
                                    continue
                                lines.append(f"  {group}:")
                                for it in items:
                                    res = (it or {}).get("result") or ""
                                    name = (it or {}).get("name") or "Check"
                                    txt = (it or {}).get("text") or ""
                                    note = (it or {}).get("note")
                                    url = (it or {}).get("url")
                                    line = f"  - [{res}] {name}: {txt}"
                                    if note:
                                        line += f" | Note: {note}"
                                    if url:
                                        line += f" | Ref: {url}"
                                    lines.append(line)
                            lines.append("")

                        # Extras
                        attachments = data.get("attachments") or []
                        if attachments:
                            lines.append("Attachments summary:")
                            for att in attachments:
                                header = (att or {}).get("header") or "Attachment"
                                txt = (att or {}).get("text") or ""
                                preview = txt.strip().splitlines()[:2]
                                preview_str = " ".join(preview).strip()
                                if len(preview_str) > 120:
                                    preview_str = preview_str[:120] + "…"
                                lines.append(f"- {header}: {preview_str}")
                            lines.append("")

                        rendered = "\n".join(lines).strip()
                        # Limit verbosity in the comment field
                        max_len = 50000
                        if len(rendered) > max_len:
                            rendered = rendered[:max_len] + "\n\n[Content truncated due to size limits]"
                        return rendered

                    details_text = render_parsed_details(parsed_json)
                    msg += "<details>\n<summary><strong>Full review details</strong></summary>\n\n"
                    msg += "```\n" + details_text + "\n```\n"
                    msg += "</details>\n\n"

                    # Attachments section if present
                    attachments = parsed_json.get("attachments") or []
                    if attachments:
                        msg += "<details>\n<summary><strong>Attachments</strong></summary>\n\n"
                        for att in attachments:
                            header = (att or {}).get("header") or "Attachment"
                            text = (att or {}).get("text") or ""
                            msg += f"### {header}\n\n"
                            # keep inside code fence to preserve formatting
                            # limit oversized attachment
                            display = text
                            if len(display) > 30000:
                                display = display[:30000] + "\n\n[Content truncated due to size limits]"
                            msg += "```\n" + display + "\n```\n\n"
                        msg += "</details>\n\n"
                    # Source link
                    msg += f"Review source: [review.json]({review_url})\n"
                else:
                    # Plain text fallback rendering with collapsible
                    max_content_length = 50000
                    content_truncated = False
                    if len(review_content) > max_content_length:
                        display = review_content[:max_content_length] + "\n\n[Content truncated due to size limits]"
                        content_truncated = True
                    else:
                        display = review_content

                    msg += (
                        f"<details>\n"
                        f"<summary><strong>Full review report (text)</strong></summary>\n\n"
                        f"```\n{display}\n```\n"
                        f"</details>\n\n"
                        f"Review source: [review.txt]({review_url})"
                    )

                # Create COPR repository installation instructions
                copr_instructions = (
                    f"## COPR Repository Setup\n\n"
                    f"To test the built packages, enable the COPR repository:\n\n"
                    f"```bash\n"
                    f"# Install dnf-plugins-core if not already installed\n"
                    f"sudo dnf install -y dnf-plugins-core\n\n"
                    f"# Enable the COPR repository\n"
                    f"dnf copr enable {self.copr_event.owner}/{self.copr_event.project_name}\n\n"
                    f"# Install the package\n"
                    f"sudo dnf install {self.copr_event.pkg}\n"
                    f"```\n\n"
                    f"**Note:** These RPMs should only be used in a testing environment.\n\n"
                )

                # Add truncation notice if content was truncated
                truncation_notice = ""
                if content_truncated:
                    truncation_notice = (
                        "\n\n**Note:** Review content has been truncated due to size limits. "
                        "View the complete report using the link below.\n"
                    )

                # Format the main message
                msg = (
                    f"## Fedora Package Review Report\n\n"
                    f"Automated fedora-review has completed for the "
                    f"**{self.copr_event.chroot}** build of **{self.copr_event.pkg}**.\n\n"
                    f"{copr_instructions}"
                    f"<details>\n"
                    f"<summary><strong>View Complete Review Report</strong></summary>\n\n"
                    f"```\n"
                    f"{review_content}\n"
                    f"```\n"
                    f"{truncation_notice}"
                    f"</details>\n\n"
                    f"**Review Report Source:** [review.txt]({review_url})"
                )

                logger.debug(
                    f"Attempting to post fedora-review comment for build {self.copr_event.build_id}"
                )
                result = self.copr_build_helper.status_reporter.comment(
                    msg,
                    duplicate_check=DuplicateCheckMode.check_last_comment,
                )
                logger.debug(f"Comment method returned: {result}")
                logger.debug(
                    f"Successfully posted fedora-review comment for build "
                    f"{self.copr_event.build_id}"
                )
            except requests.RequestException as e:
                logger.warning(f"Failed to fetch fedora-review content from {review_url}: {e}")
                # Fall back to posting just the link
                fallback_msg = (
                    f"## Fedora Package Review Report\n\n"
                    f"Automated fedora-review has completed for the "
                    f"**{self.copr_event.chroot}** build of **{self.copr_event.pkg}**.\n\n"
                    f"**Review Report:** [review.txt]({review_url})\n\n"
                    f"The review content could not be fetched automatically. "
                    f"Please check the link above for the complete report."
                )
                try:
                    self.copr_build_helper.status_reporter.comment(
                        fallback_msg,
                        duplicate_check=DuplicateCheckMode.check_last_comment,
                    )
                    logger.debug("Posted fallback fedora-review comment")
                except Exception as fallback_e:
                    logger.warning(f"Failed to post fallback fedora-review comment: {fallback_e}")
            except Exception as e:
                logger.warning(f"Failed to post fedora-review comment: {e}")
                logger.exception("Full traceback for fedora-review comment failure")
        else:
            logger.debug("Fedora-review comment skipped - conditions not met")

    def handle_testing_farm(self):
        if not self.copr_build_helper.job_tests_all:
            logger.debug("Testing farm not in the job config.")
            return

        event_dict = self.data.get_dict()

        for job_config in self.copr_build_helper.job_tests_all:
            if (
                # we need to check the labels here
                # the same way as when scheduling jobs for event
                (
                    job_config.trigger != JobConfigTriggerType.pull_request
                    or not (job_config.require.label.present or job_config.require.label.absent)
                )
                and self.copr_event.chroot
                in self.copr_build_helper.build_targets_for_test_job(job_config)
            ):
                event_dict["tests_targets_override"] = [
                    (target, job_config.identifier)
                    for target in self.copr_build_helper.build_target2test_targets_for_test_job(
                        self.copr_event.chroot,
                        job_config,
                    )
                ]
                signature(
                    AvantTaskName.testing_farm.value,
                    kwargs={
                        "package_config": dump_package_config(self.package_config),
                        "job_config": dump_job_config(job_config),
                        "event": event_dict,
                        "build_id": self.build.id,
                    },
                ).apply_async()
