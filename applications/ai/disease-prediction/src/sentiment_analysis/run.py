# Copyright (C) 2022 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions
# and limitations under the License.
#
import os
import sys

import yaml
from transformers import (
    HfArgumentParser,
    TrainingArguments
)
from transformers import logging as hf_logging

from utils import TrainerArguments, parse_arguments, DatasetConfig

hf_logging.set_verbosity_info()


def _run(args):
    kwargs = {"args": args, "training_args": args.training_args}

    if args.training_args.do_train:
        from trainer import Trainer
        trainer = Trainer(**kwargs)
        trainer.train()
    else:
        # if not do train, we do predict
        from predictor import Predictor
        predictor = Predictor(**kwargs)
        predictor.predict()


def run(args:TrainerArguments):
    if args.training_args is None:
        args.training_args = TrainingArguments()
    else:
        # load the training arguments
        _parser = HfArgumentParser(TrainingArguments)
        args.training_args = parse_arguments(_parser, args.training_args)

    if args.dataset_config is not None:
        # load the training arguments
        _parser = HfArgumentParser(DatasetConfig)
        args.dataset_config = parse_arguments(_parser, args.dataset_config)

    if args.dataset == "local" and args.dataset_config is None:
        raise ValueError("Dataset config is missing for local database.")

    _run(args)


if __name__ == "__main__":
    parser = HfArgumentParser(TrainerArguments)
    if len(sys.argv) == 2 and sys.argv[1].endswith(".json"):
        # If we pass only one argument to the script and it's the path to a json file,
        # let's parse it to get our arguments.
        json_file = os.path.abspath(sys.argv[1])
        args = parser.parse_json_file(json_file=json_file)
    elif len(sys.argv) == 2 and sys.argv[1].endswith(".yaml"):
        yaml_file = os.path.abspath(sys.argv[1])
        with open(yaml_file, "r") as f:
            args_in_yaml = yaml.safe_load(f)
        args = parser.parse_dict(args=args_in_yaml)
    else:
        args = parser.parse_args_into_dataclasses()

    print(args)

    run(args)