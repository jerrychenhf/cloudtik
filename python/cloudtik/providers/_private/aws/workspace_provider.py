import logging
from typing import Any, Dict
from cloudtik.providers._private.aws.config import  bootstrap_workspace_aws, delete_workspace_aws


from cloudtik.core.workspace_provider import WorkspaceProvider

logger = logging.getLogger(__name__)


class AWSWorkspaceProvider(WorkspaceProvider):
    def __init__(self, provider_config, workspace_name):
        WorkspaceProvider.__init__(self, provider_config, workspace_name)


    @staticmethod
    def validate_config(
            provider_config: Dict[str, Any]) -> None:
        """Required Dicts indicate that no extra fields can be introduced."""
        return None


    @staticmethod
    def bootstrap_workspace_config(cluster_config):
        return bootstrap_workspace_aws(cluster_config)


    @staticmethod
    def delete_workspace(cluster_config):
        return delete_workspace_aws(cluster_config)