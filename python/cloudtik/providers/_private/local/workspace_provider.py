import logging
from typing import Any, Dict, Optional

from cloudtik.core._private.utils import get_running_head_node, get_node_provider_of
from cloudtik.core.tags import CLOUDTIK_GLOBAL_VARIABLE_KEY, CLOUDTIK_GLOBAL_VARIABLE_KEY_PREFIX
from cloudtik.core.workspace_provider import WorkspaceProvider, Existence
from cloudtik.providers._private.local.utils import _get_tags
from cloudtik.providers._private.local.workspace_config \
    import get_workspace_head_nodes, list_local_clusters, create_local_workspace, \
    delete_local_workspace, check_local_workspace_existence, check_local_workspace_integrity, update_local_workspace, \
    bootstrap_local_workspace_config

logger = logging.getLogger(__name__)


class LocalWorkspaceProvider(WorkspaceProvider):
    def __init__(self, provider_config, workspace_name):
        WorkspaceProvider.__init__(self, provider_config, workspace_name)

    def create_workspace(self, config: Dict[str, Any]):
        """Create a workspace and all the resources needed for the workspace based on the config."""
        create_local_workspace(config)

    def delete_workspace(
            self, config: Dict[str, Any],
            delete_managed_storage: bool = False,
            delete_managed_database: bool = False):
        """Delete all the resources created for the workspace.
        Managed cloud storage is not deleted by default unless delete_managed_storage is specified.
        """
        delete_local_workspace(config)

    def update_workspace(
            self, config: Dict[str, Any],
            delete_managed_storage: bool = False,
            delete_managed_database: bool = False):
        update_local_workspace(config)

    def check_workspace_integrity(self, config: Dict[str, Any]) -> bool:
        """Check whether the workspace is correctly configured"""
        return check_local_workspace_integrity(config)

    def check_workspace_existence(self, config: Dict[str, Any]) -> Existence:
        """Check whether the workspace with the same name exists.
        The existing workspace may be in incomplete state.
        """
        return check_local_workspace_existence(config)

    def list_clusters(
            self, config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return list_local_clusters(
            self.workspace_name, self.provider_config)

    def publish_global_variables(
            self, cluster_config: Dict[str, Any],
            global_variables: Dict[str, Any]):
        # Add prefix to the variables
        global_variables_prefixed = {}
        for name in global_variables:
            prefixed_name = CLOUDTIK_GLOBAL_VARIABLE_KEY.format(name)
            global_variables_prefixed[prefixed_name] = global_variables[name]

        provider = get_node_provider_of(cluster_config)
        head_node_id = get_running_head_node(cluster_config, provider)
        provider.set_node_tags(head_node_id, global_variables_prefixed)

    def subscribe_global_variables(
            self, cluster_config: Dict[str, Any]):
        global_variables = {}
        head_nodes = get_workspace_head_nodes(
            self.workspace_name, self.provider_config)
        for head in head_nodes:
            node_tags = _get_tags(head)
            for key, value in node_tags.items():
                if key.startswith(CLOUDTIK_GLOBAL_VARIABLE_KEY_PREFIX):
                    global_variable_name = key[len(CLOUDTIK_GLOBAL_VARIABLE_KEY_PREFIX):]
                    global_variables[global_variable_name] = value

        return global_variables

    @staticmethod
    def bootstrap_workspace_config(config: Dict[str, Any]) -> Dict[str, Any]:
        """Bootstraps the workspace config by adding env defaults if needed."""
        return bootstrap_local_workspace_config(config)
