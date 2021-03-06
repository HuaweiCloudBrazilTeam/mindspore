# Copyright 2020 Huawei Technologies Co., Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ============================================================================

'''
Bert finetune and evaluation script.
'''

import os
import json
import argparse
from src.bert_for_finetune import BertFinetuneCell, BertNER
from src.finetune_eval_config import optimizer_cfg, bert_net_cfg
from src.dataset import create_ner_dataset
from src.utils import make_directory, LossCallBack, LoadNewestCkpt
from src.assessment_method import Accuracy, F1, MCC, Spearman_Correlation
import mindspore.common.dtype as mstype
from mindspore import context
from mindspore import log as logger
from mindspore.nn.wrap.loss_scale import DynamicLossScaleUpdateCell
from mindspore.nn.optim import AdamWeightDecayDynamicLR, Lamb, Momentum
from mindspore.common.tensor import Tensor
from mindspore.train.model import Model
from mindspore.train.callback import CheckpointConfig, ModelCheckpoint, TimeMonitor
from mindspore.train.serialization import load_checkpoint, load_param_into_net

_cur_dir = os.getcwd()


def do_train(dataset=None, network=None, load_checkpoint_path="", save_checkpoint_path=""):
    """ do train """
    if load_checkpoint_path == "":
        raise ValueError("Pretrain model missed, finetune task must load pretrain model!")
    steps_per_epoch = dataset.get_dataset_size()
    epoch_num = dataset.get_repeat_count()
    # optimizer
    if optimizer_cfg.optimizer == 'AdamWeightDecayDynamicLR':
        optimizer = AdamWeightDecayDynamicLR(network.trainable_params(),
                                             decay_steps=steps_per_epoch * epoch_num,
                                             learning_rate=optimizer_cfg.AdamWeightDecayDynamicLR.learning_rate,
                                             end_learning_rate=optimizer_cfg.AdamWeightDecayDynamicLR.end_learning_rate,
                                             power=optimizer_cfg.AdamWeightDecayDynamicLR.power,
                                             warmup_steps=int(steps_per_epoch * epoch_num * 0.1),
                                             weight_decay=optimizer_cfg.AdamWeightDecayDynamicLR.weight_decay,
                                             eps=optimizer_cfg.AdamWeightDecayDynamicLR.eps)
    elif optimizer_cfg.optimizer == 'Lamb':
        optimizer = Lamb(network.trainable_params(), decay_steps=steps_per_epoch * epoch_num,
                         start_learning_rate=optimizer_cfg.Lamb.start_learning_rate,
                         end_learning_rate=optimizer_cfg.Lamb.end_learning_rate,
                         power=optimizer_cfg.Lamb.power, weight_decay=optimizer_cfg.Lamb.weight_decay,
                         warmup_steps=int(steps_per_epoch * epoch_num * 0.1),
                         decay_filter=optimizer_cfg.Lamb.decay_filter)
    elif optimizer_cfg.optimizer == 'Momentum':
        optimizer = Momentum(network.trainable_params(), learning_rate=optimizer_cfg.Momentum.learning_rate,
                             momentum=optimizer_cfg.Momentum.momentum)
    else:
        raise Exception("Optimizer not supported. support: [AdamWeightDecayDynamicLR, Lamb, Momentum]")

    # load checkpoint into network
    ckpt_config = CheckpointConfig(save_checkpoint_steps=steps_per_epoch, keep_checkpoint_max=1)
    ckpoint_cb = ModelCheckpoint(prefix="ner", directory=save_checkpoint_path, config=ckpt_config)
    param_dict = load_checkpoint(load_checkpoint_path)
    load_param_into_net(network, param_dict)

    update_cell = DynamicLossScaleUpdateCell(loss_scale_value=2**32, scale_factor=2, scale_window=1000)
    netwithgrads = BertFinetuneCell(network, optimizer=optimizer, scale_update_cell=update_cell)
    model = Model(netwithgrads)
    callbacks = [TimeMonitor(dataset.get_dataset_size()), LossCallBack(), ckpoint_cb]
    model.train(epoch_num, dataset, callbacks=callbacks)

def eval_result_print(assessment_method="accuracy", callback=None):
    """print eval result"""
    if assessment_method == "accuracy":
        print("acc_num {} , total_num {}, accuracy {:.6f}".format(callback.acc_num, callback.total_num,
                                                                  callback.acc_num / callback.total_num))
    elif assessment_method == "f1":
        print("Precision {:.6f} ".format(callback.TP / (callback.TP + callback.FP)))
        print("Recall {:.6f} ".format(callback.TP / (callback.TP + callback.FN)))
        print("F1 {:.6f} ".format(2 * callback.TP / (2 * callback.TP + callback.FP + callback.FN)))
    elif assessment_method == "mcc":
        print("MCC {:.6f} ".format(callback.cal()))
    elif assessment_method == "spearman_correlation":
        print("Spearman Correlation is {:.6f} ".format(callback.cal()[0]))
    else:
        raise ValueError("Assessment method not supported, support: [accuracy, f1, mcc, spearman_correlation]")

def do_eval(dataset=None, network=None, use_crf="", num_class=2, assessment_method="accuracy", data_file="",
            load_checkpoint_path="", vocab_file="", label2id_file="", tag_to_index=None):
    """ do eval """
    if load_checkpoint_path == "":
        raise ValueError("Finetune model missed, evaluation task must load finetune model!")
    if assessment_method == "clue_benchmark":
        bert_net_cfg.batch_size = 1
    net_for_pretraining = network(bert_net_cfg, False, num_class, use_crf=(use_crf.lower() == "true"),
                                  tag_to_index=tag_to_index)
    net_for_pretraining.set_train(False)
    param_dict = load_checkpoint(load_checkpoint_path)
    load_param_into_net(net_for_pretraining, param_dict)
    model = Model(net_for_pretraining)

    if assessment_method == "clue_benchmark":
        from src.cluener_evaluation import submit
        submit(model=model, path=data_file, vocab_file=vocab_file, use_crf=use_crf, label2id_file=label2id_file)
    else:
        if assessment_method == "accuracy":
            callback = Accuracy()
        elif assessment_method == "f1":
            callback = F1((use_crf.lower() == "true"), num_class)
        elif assessment_method == "mcc":
            callback = MCC()
        elif assessment_method == "spearman_correlation":
            callback = Spearman_Correlation()
        else:
            raise ValueError("Assessment method not supported, support: [accuracy, f1, mcc, spearman_correlation]")

        columns_list = ["input_ids", "input_mask", "segment_ids", "label_ids"]
        for data in dataset.create_dict_iterator():
            input_data = []
            for i in columns_list:
                input_data.append(Tensor(data[i]))
            input_ids, input_mask, token_type_id, label_ids = input_data
            logits = model.predict(input_ids, input_mask, token_type_id, label_ids)
            callback.update(logits, label_ids)
        print("==============================================================")
        eval_result_print(assessment_method, callback)
        print("==============================================================")

def run_ner():
    """run ner task"""
    parser = argparse.ArgumentParser(description="run classifier")
    parser.add_argument("--device_target", type=str, default="Ascend", help="Device type, default is Ascend")
    parser.add_argument("--assessment_method", type=str, default="accuracy", help="assessment_method include: "
                                                                                  "[F1, clue_benchmark], default is F1")
    parser.add_argument("--do_train", type=str, default="false", help="Eable train, default is false")
    parser.add_argument("--do_eval", type=str, default="false", help="Eable eval, default is false")
    parser.add_argument("--use_crf", type=str, default="false", help="Use crf, default is false")
    parser.add_argument("--device_id", type=int, default=0, help="Device id, default is 0.")
    parser.add_argument("--epoch_num", type=int, default="1", help="Epoch number, default is 1.")
    parser.add_argument("--num_class", type=int, default="2", help="The number of class, default is 2.")
    parser.add_argument("--vocab_file_path", type=str, default="", help="Vocab file path, used in clue benchmark")
    parser.add_argument("--label2id_file_path", type=str, default="", help="label2id file path, used in clue benchmark")
    parser.add_argument("--save_finetune_checkpoint_path", type=str, default="", help="Save checkpoint path")
    parser.add_argument("--load_pretrain_checkpoint_path", type=str, default="", help="Load checkpoint file path")
    parser.add_argument("--load_finetune_checkpoint_path", type=str, default="", help="Load checkpoint file path")
    parser.add_argument("--train_data_file_path", type=str, default="",
                        help="Data path, it is better to use absolute path")
    parser.add_argument("--eval_data_file_path", type=str, default="",
                        help="Data path, it is better to use absolute path")
    parser.add_argument("--schema_file_path", type=str, default="",
                        help="Schema path, it is better to use absolute path")
    args_opt = parser.parse_args()
    epoch_num = args_opt.epoch_num
    assessment_method = args_opt.assessment_method.lower()
    load_pretrain_checkpoint_path = args_opt.load_pretrain_checkpoint_path
    save_finetune_checkpoint_path = args_opt.save_finetune_checkpoint_path
    load_finetune_checkpoint_path = args_opt.load_finetune_checkpoint_path

    if args_opt.do_train.lower() == "false" and args_opt.do_eval.lower() == "false":
        raise ValueError("At least one of 'do_train' or 'do_eval' must be true")
    if args_opt.do_train.lower() == "true" and args_opt.train_data_file_path == "":
        raise ValueError("'train_data_file_path' must be set when do finetune task")
    if args_opt.do_eval.lower() == "true" and args_opt.eval_data_file_path == "":
        raise ValueError("'eval_data_file_path' must be set when do evaluation task")
    if args_opt.assessment_method.lower() == "clue_benchmark" and args_opt.vocab_file_path == "":
        raise ValueError("'vocab_file_path' must be set to do clue benchmark")
    if args_opt.use_crf.lower() == "true" and args_opt.label2id_file_path == "":
        raise ValueError("'label2id_file_path' must be set to use crf")
    if args_opt.assessment_method.lower() == "clue_benchmark" and args_opt.label2id_file_path == "":
        raise ValueError("'label2id_file_path' must be set to do clue benchmark")

    target = args_opt.device_target
    if target == "Ascend":
        context.set_context(mode=context.GRAPH_MODE, device_target="Ascend", device_id=args_opt.device_id)
    elif target == "GPU":
        context.set_context(mode=context.GRAPH_MODE, device_target="GPU")
        if bert_net_cfg.compute_type != mstype.float32:
            logger.warning('GPU only support fp32 temporarily, run with fp32.')
            bert_net_cfg.compute_type = mstype.float32
    else:
        raise Exception("Target error, GPU or Ascend is supported.")

    tag_to_index = None
    if args_opt.use_crf.lower() == "true":
        with open(args_opt.label2id_file_path) as json_file:
            tag_to_index = json.load(json_file)
        max_val = max(tag_to_index.values())
        tag_to_index["<START>"] = max_val + 1
        tag_to_index["<STOP>"] = max_val + 2
        number_labels = len(tag_to_index)
    else:
        number_labels = args_opt.num_class
    netwithloss = BertNER(bert_net_cfg, True, num_labels=number_labels,
                          use_crf=(args_opt.use_crf.lower() == "true"),
                          tag_to_index=tag_to_index, dropout_prob=0.1)
    if args_opt.do_train.lower() == "true":
        ds = create_ner_dataset(batch_size=bert_net_cfg.batch_size, repeat_count=epoch_num,
                                assessment_method=assessment_method, data_file_path=args_opt.train_data_file_path,
                                schema_file_path=args_opt.schema_file_path)
        do_train(ds, netwithloss, load_pretrain_checkpoint_path, save_finetune_checkpoint_path)

        if args_opt.do_eval.lower() == "true":
            if save_finetune_checkpoint_path == "":
                load_finetune_checkpoint_dir = _cur_dir
            else:
                load_finetune_checkpoint_dir = make_directory(save_finetune_checkpoint_path)
            load_finetune_checkpoint_path = LoadNewestCkpt(load_finetune_checkpoint_dir,
                                                           ds.get_dataset_size(), epoch_num, "ner")

    if args_opt.do_eval.lower() == "true":
        ds = create_ner_dataset(batch_size=bert_net_cfg.batch_size, repeat_count=epoch_num,
                                assessment_method=assessment_method, data_file_path=args_opt.eval_data_file_path,
                                schema_file_path=args_opt.schema_file_path)
        do_eval(ds, BertNER, args_opt.use_crf, number_labels, assessment_method, args_opt.eval_data_file_path,
                load_finetune_checkpoint_path, args_opt.vocab_file_path, args_opt.label2id_file_path, tag_to_index)

if __name__ == "__main__":
    run_ner()
