import argparse

from cloudtik.core._private.util.runtime_utils import subscribe_nodes_info
from cloudtik.runtime.zookeeper.scripting import update_configurations, configure_server_ensemble


def main():
    parser = argparse.ArgumentParser(
        description="Configuring runtime.")
    parser.add_argument(
        '--head', action='store_true', default=False,
        help='Configuring for head node.')
    args = parser.parse_args()

    # Configure the server ensemble
    if not args.head:
        # Update configuration from runtime config
        update_configurations()

        nodes_info = subscribe_nodes_info()
        configure_server_ensemble(nodes_info)


if __name__ == "__main__":
    main()
