# Modifications Copyright (C) 2023 Intel Corporation
# SPDX-License-Identifier: MIT

import argparse
import random

import dgl
import numpy as np
import torch as th

from cloudtik.runtime.ai.modeling.graph_modeling.graph_sage.modeling.model.\
    homogeneous.transductive.distributed.trainer import Trainer


def main(args):
    seed = 7
    print("random seed set to: ", seed)
    random.seed(seed)
    np.random.seed(seed)
    dgl.seed(seed)
    dgl.random.seed(seed)
    th.random.manual_seed(seed)
    th.manual_seed(seed)

    # load original full graph to get the train/test/val id sets
    print("Loading original data to get the global train/test/val masks")
    dataset = dgl.data.CSVDataset(args.dataset_dir, force_reload=False)

    trainer = Trainer(args)
    trainer.train(dataset)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Graph SAGE Distributed")
    parser.add_argument(
        "--dataset-dir", "--dataset_dir",
        type=str,
        help="input dir with CSVDataset files"
    )
    parser.add_argument(
        "--model-file", "--model_file",
        type=str,
        help="output for model /your_path/model_graphsage_2L_64.pt",
    )
    parser.add_argument(
        "--node-embeddings-file", "--node_embeddings_file",
        type=str,
        help="node embeddings output: /your_path/node_emb.pt",
    )

    parser.add_argument(
        "--graph-name", "--graph_name",
        type=str,
        help="graph name")
    parser.add_argument(
        "--id",
        type=int,
        help="the partition id")
    parser.add_argument(
        "--ip-config", "--ip_config",
        type=str,
        help="The file for IP configuration")
    parser.add_argument(
        "--part-config", "--part_config",
        type=str,
        help="The path to the partition config file"
    )
    parser.add_argument(
        "--n-classes", "--n_classes",
        type=int,
        help="the number of classes")
    parser.add_argument(
        "--num-gpus", "--num_gpus",
        type=int, default=-1,
        help="the number of GPU device. Use -1 for CPU training",
    )

    parser.add_argument("--num-epochs", "--num_epochs",
                        type=int, default=3)
    parser.add_argument("--num-hidden", "--num_hidden",
                        type=int, default=16)
    parser.add_argument("--num-layers", "--num_layers",
                        type=int, default=2)
    parser.add_argument("--fan-out", "--fan_out",
                        type=str, default="10,25")
    parser.add_argument("--batch-size", "--batch_size",
                        type=int, default=1000)
    parser.add_argument("--batch-size-eval", "--batch_size_eval",
                        type=int, default=100000)
    parser.add_argument("--eval-every", "--eval_every",
                        type=int, default=5)
    parser.add_argument("--lr",
                        type=float, default=0.003)

    parser.add_argument("--log-every", "--log_every",
                        type=int, default=20)

    parser.add_argument(
        "--local-rank", "--local_rank",
        type=int,
        help="get rank of the process")
    parser.add_argument(
        "--standalone",
        action="store_true",
        help="run in the standalone mode"
    )
    parser.add_argument(
        "--num-negs", "--num_negs",
        type=int, default=1)
    parser.add_argument(
        "--neg-share", "--neg_share",
        default=False, action="store_true",
        help="sharing neg nodes for positive nodes",
    )
    parser.add_argument(
        "--remove-edge", "--remove_edge",
        default=False, action="store_true",
        help="whether to remove edges during sampling",
    )
    parser.add_argument(
        "--dgl-sparse", "--dgl_sparse",
        action="store_true",
        help="Whether to use DGL sparse embedding",
    )
    parser.add_argument(
        "--sparse-lr", "--sparse_lr",
        type=float, default=1e-2,
        help="sparse lr rate")

    args = parser.parse_args()
    print(args)

    main(args)