# Copyright Contributors to the Packit project.
# SPDX-License-Identifier: MIT

"""
Hello World command handler for demonstrating the packit-service handler architecture.
"""

import logging
from typing import Optional

from packit_service.events.forgejo.issue import Comment as ForgejoIssueComment
from packit_service.worker.handlers.abstract import (
    NewPackageHandler,
    TaskName,
    run_for_comment_new_package,
    reacts_to_new_package,
)
from packit_service.worker.result import TaskResults

logger = logging.getLogger(__name__)


@run_for_comment_new_package(command="hello-world")
@reacts_to_new_package(ForgejoIssueComment)
class HelloWorldNewPackageHandler(NewPackageHandler):
    """
    A simple hello-world handler that demonstrates the basic structure 
    of a new_package command handler in packit-service.
    
    This handler can be triggered by commenting `/packit hello-world` 
    in a Forgejo issue.
    """
    
    task_name = TaskName.hello_world

    def __init__(self, event: dict, **kwargs):
        """
        Initialize the HelloWorld handler.
        
        Args:
            event: Event dictionary containing the comment information
            **kwargs: Additional keyword arguments (for compatibility)
        """
        # Following the pattern from NewPackageRepositoryHandler,
        # we don't call super().__init__() for new_package handlers
        self.event = event
        
        # Extract comment information if available
        self.comment = event.get("comment", "")
        self.actor = event.get("actor", "unknown")
        self.issue_id = event.get("issue_id", "unknown")

    def run(self) -> TaskResults:
        """
        Execute the hello-world command.
        
        This is a simple demonstration handler that logs a greeting message
        and returns success.
        
        Returns:
            TaskResults: Result of the hello-world operation
        """
        try:
            logger.info("🌟 Hello World handler started!")
            logger.info(f"📝 Comment: {self.comment}")
            logger.info(f"👤 Actor: {self.actor}")
            logger.info(f"🎫 Issue ID: {self.issue_id}")
            
            # Simulate some work
            greeting_message = f"Hello, {self.actor}! 👋"
            logger.info(f"✨ {greeting_message}")
            
            # Log some system information for demonstration
            logger.info("🔧 Hello World handler is working correctly!")
            logger.info("📊 System status: All good!")
            
            return TaskResults(
                success=True,
                details={
                    "msg": "Hello World command executed successfully! 🎉",
                    "greeting": greeting_message,
                    "actor": self.actor,
                    "issue_id": self.issue_id,
                    "comment": self.comment,
                    "handler": self.__class__.__name__
                }
            )
            
        except Exception as e:
            error_msg = f"Hello World handler failed: {str(e)}"
            logger.error(f"❌ {error_msg}")
            
            return TaskResults(
                success=False,
                details={
                    "msg": error_msg,
                    "error": str(e),
                    "handler": self.__class__.__name__
                }
            )

    def run_job(self) -> dict:
        """
        Run the job for the hello-world handler.
        
        This method is called by the task system and delegates to the run() method.
        
        Returns:
            Dict containing the job results
        """
        logger.info("🚀 Starting Hello World job...")
        result = self.run()
        # TaskResults behaves like a dict, so we access success as a key
        logger.info(f"✅ Hello World job completed with success: {result['success']}")
        
        return {
            "hello-world": result
        }

    @property
    def clean_api(self):
        """For compatibility with the base class."""
        return None

    @property
    def project(self):
        """For compatibility with the base class."""
        return None

    @property
    def project_url(self):
        """For compatibility with the base class."""
        return None

    @property
    def service_config(self):
        """For compatibility with the base class."""
        from packit_service.config import ServiceConfig
        return ServiceConfig.get_service_config()

    @property
    def packit_api(self):
        """For compatibility with the base class."""
        return None
