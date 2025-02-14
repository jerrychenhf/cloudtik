import logging
from typing import Any, Dict

from cloudtik.core._private.runtime_factory import BUILT_IN_RUNTIME_NONE, BUILT_IN_RUNTIME_CONSUL
from cloudtik.core.node_provider import NodeProvider
from cloudtik.runtime.common.runtime_base import RuntimeBase
from cloudtik.runtime.coredns.utils import _get_runtime_processes, \
    _get_runtime_services, _with_runtime_environment_variables

logger = logging.getLogger(__name__)


class CoreDNSRuntime(RuntimeBase):
    """Implementation for CoreDNS Runtime for a DNS Server
    which resolves domain names for both local and upstream"""

    def __init__(self, runtime_config: Dict[str, Any]) -> None:
        super().__init__(runtime_config)

    def with_environment_variables(
            self, config: Dict[str, Any], provider: NodeProvider,
            node_id: str) -> Dict[str, Any]:
        """Export necessary runtime environment variables for running node commands.
        For example: {"ENV_NAME": value}
        """
        return _with_runtime_environment_variables(
            self.runtime_config, config=config)

    def get_runtime_services(self, cluster_config: Dict[str, Any]):
        return _get_runtime_services(self.runtime_config, cluster_config)

    @staticmethod
    def get_processes():
        return _get_runtime_processes()

    @staticmethod
    def get_dependencies():
        return [
            BUILT_IN_RUNTIME_NONE,
            BUILT_IN_RUNTIME_CONSUL,
        ]
