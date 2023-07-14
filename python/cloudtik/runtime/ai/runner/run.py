import argparse
import glob
import logging
import os
import platform
from argparse import ArgumentParser
from argparse import RawTextHelpFormatter
from datetime import datetime

from cloudtik.runtime.ai.runner.util.distributor import Distributor

logger = logging.getLogger(__name__)

r"""
This is a launch program for running local or distributed training and inference program.

This launch program can wrapper different launch methods and provide a abstracted view of launching
python program.

For the launching on CPU clusters, the script optimizes the configuration of thread and memory
management. For thread management, the script configures thread affinity and the preload of Intel OMP library.
For memory management, it configures NUMA binding and preload optimized memory allocation library (e.g. tcmalloc, jemalloc).

**How to use this module:**

*** Local single-instance inference/training ***

1. Run single-instance inference or training on a single node with all CPU nodes.

::

   >>> cloudtik-run --throughput_mode script.py args

2. Run single-instance inference or training on a single CPU node.

::

   >>> cloudtik-run --node_id 1 script.py args

*** Local multi-instance inference ***

1. Multi-instance
   By default, one instance per node. if you want to set the instance numbers and core per instance,
   --ninstances and --ncore_per_instance should be set.


   >>> cloudtik-run -- python_script args

   eg: on CLX8280 with 14 instance, 4 cores per instance
::

   >>> cloudtik-run --ninstances 14 --ncore_per_instance 4 python_script args

2. Run single-instance inference among multiple instances.
   By default, runs all ninstances. If you want to independently run a single instance among ninstances, specify instance_idx.

   eg: run 0th instance among SKX with 2 instance (i.e., numactl -C 0-27)
::

   >>> cloudtik-run --ninstances 2 --instance_idx 0 python_script args

   eg: run 1st instance among SKX with 2 instance (i.e., numactl -C 28-55)
::

   >>> cloudtik-run --ninstances 2 --instance_idx 1 python_script args

   eg: run 0th instance among SKX with 2 instance, 2 cores per instance, first four cores (i.e., numactl -C 0-1)
::

   >>> cloudtik-run --core_list "0, 1, 2, 3" --ninstances 2 --ncore_per_instance 2 --instance_idx 0 python_script args

*** Distributed Training ***

spawns up multiple distributed training processes on each of the training nodes. For intel_extension_for_pytorch, oneCCL
is used as the communication backend and MPI used to launch multi-proc. To get the better
performance, you should specify the different cores for oneCCL communication and computation
process separately. This tool can automatically set these ENVs(such as I_MPI_PIN_DOMIN) and launch
multi-proc for you.

The utility can be used for single-node distributed training, in which one or
more processes per node will be spawned.  It can also be used in
multi-node distributed training, by spawning up multiple processes on each node
for well-improved multi-node distributed training performance as well.


1. Single-Node multi-process distributed training

::

    >>> cloudtik-run --distributed  python_script  --arg1 --arg2 --arg3 and all other
                arguments of your training script

2. Multi-Node multi-process distributed training: (e.g. two nodes)

::

    >>> cloudtik-run --nproc_per_node=xxx
               --nnodes=2 --hosts ip1,ip2 python_sript --arg1 --arg2 --arg3
               and all other arguments of your training script)
"""


def add_cpu_option_params(parser):
    group = parser.add_argument_group("Parameters for CPU options")
    group.add_argument("--use-logical-core", "--use_logical_core",
                       action='store_true', default=False,
                       help="Whether only use physical cores")


def add_distributed_training_params(parser):
    group = parser.add_argument_group("Distributed Training Parameters")
    group.add_argument('--num-proc', '--num_proc',
                       action='store', type=int, default=0,
                       help="The number of process to run for distributed training")
    group.add_argument("--nnodes",
                       type=int, default=0,
                       help="The number of nodes to use for distributed training")
    group.add_argument("--nproc-per-node", "--nproc_per_node",
                       action='store', type=int, default=0,
                       help="The number of processes to launch on each node")
    group.add_argument("--hosts",
                       default="", type=str,
                       help="List of hosts separated with comma for launching tasks. "
                            "When hosts is specified, it implies distributed training. "
                            "node address which should be either the IP address"
                            "or the hostname with or without slots.")
    group.add_argument("--hostfile",
                       default="", type=str,
                       help="Hostfile is necessary for multi-node multi-proc "
                            "training. hostfile includes the node address list "
                            "node address which should be either the IP address"
                            "or the hostname with or without slots.")

    # ccl control
    group.add_argument("--ccl-worker-count", "--ccl_worker_count",
                       action='store', dest='ccl_worker_count', default=4, type=int,
                       help="Core numbers per rank used for ccl communication")
    # mpi control
    group.add_argument("--master-addr", "--master_addr",
                       action='store', default="127.0.0.1", type=str,
                       help="Master node (rank 0)'s address, should be either "
                            "the IP address or the hostname of node 0, for "
                            "single node multi-proc training, the "
                            "--master_addr can simply be 127.0.0.1")
    group.add_argument("--master-port", "--master_port",
                       action='store', default=29500, type=int,
                       help="Master node (rank 0)'s free port that needs to "
                            "be used for communication during distributed "
                            "training")
    group.add_argument("--mpi-args", "--mpi_args", "--more_mpi_params",
                       action='store', dest='mpi_args', default="", type=str,
                       help="User can pass more parameters for mpiexec.hydra "
                            "except for -np -ppn -hostfile and -genv I_MPI_PIN_DOMAIN")


def add_memory_allocator_params(parser):
    group = parser.add_argument_group("Memory Allocator Parameters")
    # allocator control
    group.add_argument("--enable-tcmalloc", "--enable_tcmalloc",
                       action='store_true', default=False,
                       help="Enable tcmalloc allocator")
    group.add_argument("--enable-jemalloc", "--enable_jemalloc",
                       action='store_true', default=False,
                       help="Enable jemalloc allocator")
    group.add_argument("--use-default-allocator", "--use_default_allocator",
                       action='store_true', default=False,
                       help="Use default memory allocator")


def add_local_launcher_params(parser):
    group = parser.add_argument_group("Local Instance Launching Parameters")
    # instances control
    group.add_argument("--ninstances",
                       default=-1, type=int,
                       help="The number of instances to run local. "
                            "You should give the cores number you used for per instance.")
    group.add_argument("--ncore-per-instance", "--ncore_per_instance",
                       default=-1, type=int,
                       help="Cores per instance")
    group.add_argument("--skip-cross-node-cores", "--skip_cross_node_cores",
                       action='store_true', default=False,
                       help="If specified --ncore_per_instance, skips cross-node cores.")
    group.add_argument("--instance-idx", "--instance_idx",
                       default="-1", type=int,
                       help="Specify instance index to assign ncores_per_instance for instance_idx; "
                            "otherwise ncore_per_instance will be assigned sequentially to ninstances.")
    group.add_argument("--latency-mode", "--latency_mode",
                       action='store_true', default=False,
                       help="By default 4 core per instance and use all physical cores")
    group.add_argument("--throughput-mode", "--throughput_mode",
                       action='store_true', default=False,
                       help="By default one instance per node and use all physical cores")
    group.add_argument("--node-id", "--node_id",
                       default=-1, type=int,
                       help="node id for the current instance, by default all nodes will be used")
    group.add_argument("--disable-numactl", "--disable_numactl",
                       action='store_true', default=False,
                       help="Disable numactl")
    group.add_argument("--disable-taskset", "--disable_taskset",
                       action='store_true', default=False,
                       help="Disable taskset")
    group.add_argument("--core-list", "--core_list",
                       default=None, type=str,
                       help="Specify the core list as 'core_id, core_id, ....', otherwise, all the cores will be used.")
    group.add_argument("--benchmark",
                       action='store_true', default=False,
                       help="Enable benchmark config. JeMalloc's MALLOC_CONF has been tuned for low latency. "
                            "Recommend to use this for benchmarking purpose; for other use cases, "
                            "this MALLOC_CONF may cause Out-of-Memory crash.")


def add_kmp_iomp_params(parser):
    group = parser.add_argument_group("IOMP Parameters")
    group.add_argument("--disable-iomp", "--disable_iomp",
                       action='store_true', default=False,
                       help="By default, we use Intel OpenMP and libiomp5.so will be add to LD_PRELOAD")


def add_auto_ipex_params(parser, auto_ipex_default_enabled=False):
    group = parser.add_argument_group("Code_Free Parameters")
    group.add_argument("--auto-ipex", "--auto_ipex",
                       action='store_true', default=auto_ipex_default_enabled,
                       help="Auto enabled the ipex optimization feature")
    group.add_argument("--dtype",
                       default="float32", type=str,
                       choices=['float32', 'bfloat16'],
                       help="The data type to run inference. float32 or bfloat16 is allowed.")
    group.add_argument("--auto-ipex-verbose", "--auto_ipex_verbose",
                       action='store_true', default=False,
                       help="This flag is only used for debug and UT of auto ipex.")
    group.add_argument("--disable-ipex-graph-mode", "--disable_ipex_graph_mode",
                       action='store_true', default=False,
                       help="Enable the Graph Mode for ipex.optimize")


def make_nic_action():
    # This is an append Action that splits the values on ','
    class NicAction(argparse.Action):
        def __init__(self,
                     option_strings,
                     dest,
                     default=None,
                     type=None,
                     choices=None,
                     required=False,
                     help=None):
            super(NicAction, self).__init__(
                option_strings=option_strings,
                dest=dest,
                nargs=1,
                default=default,
                type=type,
                choices=choices,
                required=required,
                help=help)

        def __call__(self, parser, args, values, option_string=None):
            if ',' in values[0]:
                values = values[0].split(',')

            # union the existing dest nics with the new ones
            items = getattr(args, self.dest, None)
            items = set() if items is None else items
            items = items.union(values)

            setattr(args, self.dest, items)

    return NicAction


def add_horovod_params(parser):
    group = parser.add_argument_group("Horovod Parameters")
    group.add_argument('--gloo',
                       action='store_true', dest='use_gloo',
                       help='Run Horovod using the Gloo controller. This will '
                            'be the default if Horovod was not built with MPI support.')
    group.add_argument('--mpi',
                       action='store_true', dest='use_mpi',
                       help='Run Horovod using the MPI controller. This will '
                            'be the default if Horovod was built with MPI support.')

    group.add_argument('--network-interfaces', '--network_interfaces',
                       action=make_nic_action(), dest='nics',
                       help='Network interfaces that can be used for communication separated by '
                            'comma. If not specified, will find the common NICs among all '
                            'the workers. Example: --network-interfaces "eth0,eth1".')

    group.add_argument('--output-filename', '--output_filename',
                       action='store',
                       help='For Gloo, writes stdout / stderr of all processes to a filename of the form '
                            '<output_filename>/rank.<rank>/<stdout | stderr>. The <rank> will be padded with 0 '
                            'characters to ensure lexicographical order. For MPI, delegates its behavior to mpirun.')


def parse_args():
    """
    Helper function parsing the command line options
    @retval ArgumentParser
    """
    parser = ArgumentParser(
        description="This is a program for launching local or distributed training and inference."
                    "\n################################# Basic usage ############################# \n"
                    "\n1. Local single-instance training or inference\n"
                    "\n   >>> cloudtik-run python_script args \n"
                    "\n2. Local multi-instance inference \n"
                    "\n    >>> cloudtik-run --ninstances 2 --ncore_per_instance 8 python_script args\n"
                    "\n3. Single-Node multi-process distributed training\n"
                    "\n    >>> cloudtik-run --distributed  python_script args\n"
                    "\n4. Multi-Node multi-process distributed training: (e.g. two nodes)\n"
                    "\n   >>> cloudtik-run --nproc_per_node=2\n"
                    "\n       --nnodes=2 --hosts ip1,ip2 python_script args\n"
                    "\n############################################################################# \n",
                    formatter_class=RawTextHelpFormatter)

    parser.add_argument('--distributed',
                        action='store_true', default=False,
                        help='Enable distributed training.')
    parser.add_argument("--launcher",
                        default="", type=str,
                        help="The launcher to use: default, optimized, horovod")
    parser.add_argument("-m", "--module",
                        default=False, action="store_true",
                        help="Changes each process to interpret the launch script "
                             "as a python module, executing with the same behavior as"
                             "'python -m'.")

    parser.add_argument("--no-python", "--no_python",
                        default=False, action="store_true",
                        help="Do not prepend the --program script with \"python\" - just exec "
                             "it directly. Useful when the script is not a Python script.")

    parser.add_argument("--log-path", "--log_path",
                        default="", type=str,
                        help="The log file directory. Default path is '', which means disable logging to files.")
    parser.add_argument("--log-file-prefix", "--log_file_prefix",
                        default="run", type=str,
                        help="log file prefix")

    parser.add_argument("--verbose",
                        default=False, action='store_true',
                        help='If this flag is set, extra messages will be printed.')

    add_distributed_training_params(parser)
    add_local_launcher_params(parser)

    add_cpu_option_params(parser)
    add_memory_allocator_params(parser)
    add_kmp_iomp_params(parser)
    add_auto_ipex_params(parser)

    add_horovod_params(parser)

    parser.add_argument('command', nargs=argparse.REMAINDER,
                        help='Command to be executed.')

    args = parser.parse_args()
    args.run_func = None
    args.executable = None
    return args


def _verify_ld_preload():
    if "LD_PRELOAD" in os.environ:
        lst_valid = []
        tmp_ldpreload = os.environ["LD_PRELOAD"]
        for item in tmp_ldpreload.split(":"):
            if item != "":
                matches = glob.glob(item)
                if len(matches) > 0:
                    lst_valid.append(item)
                else:
                    logger.warning("{} doesn't exist. Removing it from LD_PRELOAD.".format(item))
        if len(lst_valid) > 0:
            os.environ["LD_PRELOAD"] = ":".join(lst_valid)
        else:
            os.environ["LD_PRELOAD"] = ""


def _setup_logger(args):
    format_str = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    logging.basicConfig(level=logging.INFO, format=format_str)

    root_logger = logging.getLogger("")
    root_logger.setLevel(logging.INFO)

    if args.log_path:
        path = os.path.dirname(args.log_path if args.log_path.endswith('/') else args.log_path + '/')
        if not os.path.exists(path):
            os.makedirs(path)
        args.log_path = path
        args.log_file_prefix = '{}_{}'.format(args.log_file_prefix, datetime.now().strftime("%Y%m%d%H%M%S"))

        fileHandler = logging.FileHandler("{0}/{1}_instances.log".format(args.log_path, args.log_file_prefix))
        logFormatter = logging.Formatter(format_str)
        fileHandler.setFormatter(logFormatter)

        # add the handle to root logger
        root_logger.addHandler(fileHandler)


def _run(args):
    # check either command or func be specified
    if not args.command and not args.run_func:
        raise ValueError("Must specify either command or function to launch.")

    distributor = Distributor(
        args.num_proc,
        args.nnodes,
        args.nproc_per_node,
        args.hosts,
        args.hostfile,
    )

    if distributor.distributed:
        args.distributed = True

    if not args.distributed:
        if args.latency_mode and args.throughput_mode:
            raise RuntimeError("Either args.latency_mode or args.throughput_mode should be set")

    env_before = set(os.environ.keys())

    # Verify LD_PRELOAD
    _verify_ld_preload()

    if args.distributed:
        if args.launcher == "default":
            from cloudtik.runtime.ai.runner.cpu.default_training_launcher \
                import DefaultTrainingLauncher
            launcher = DefaultTrainingLauncher(args, distributor)
        elif args.launcher == "horovod":
            from cloudtik.runtime.ai.runner.horovod_training_launcher \
                import HorovodTrainingLauncher
            launcher = HorovodTrainingLauncher(args, distributor)
        else:
            from cloudtik.runtime.ai.runner.cpu.optimized_training_launcher \
                import OptimizedTrainingLauncher
            launcher = OptimizedTrainingLauncher(args, distributor)
    else:
        from cloudtik.runtime.ai.runner.cpu.local_launcher \
            import LocalLauncher
        launcher = LocalLauncher(args, distributor)

    launcher.launch()

    for x in sorted(set(os.environ.keys()) - env_before):
        logger.debug('{0}={1}'.format(x, os.environ[x]))


def main():
    if platform.system() == "Windows":
        raise RuntimeError("Windows platform is not supported!!!")

    args = parse_args()
    _setup_logger(args)

    _run(args)


if __name__ == "__main__":
    main()