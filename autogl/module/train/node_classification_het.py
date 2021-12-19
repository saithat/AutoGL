"""
Node classification Het Trainer Implementation
"""

from . import register_trainer

from .base import BaseNodeClassificationHetTrainer, EarlyStopping
import torch
from torch.optim.lr_scheduler import (
    StepLR,
    MultiStepLR,
    ExponentialLR,
    ReduceLROnPlateau,
)
import torch.nn.functional as F
import numpy as np
from ..model import MODEL_DICT, BaseModel
from .evaluation import get_feval, Logloss
from typing import Union
from copy import deepcopy
from sklearn.metrics import f1_score

from ...utils import get_logger

from ...backend import DependentBackend

LOGGER = get_logger("node classification het trainer")

def score(logits, labels):
    _, indices = torch.max(logits, dim=1)
    prediction = indices.long().cpu().numpy()
    labels = labels.cpu().numpy()

    accuracy = (prediction == labels).sum() / len(prediction)
    micro_f1 = f1_score(labels, prediction, average='micro')
    macro_f1 = f1_score(labels, prediction, average='macro')

    return accuracy, micro_f1, macro_f1

@register_trainer("NodeClassificationHet")
class NodeClassificationHetTrainer(BaseNodeClassificationHetTrainer):
    """
    The node classification trainer.
    Used to automatically train the node classification problem.
    Parameters
    ----------
    model: ``BaseModel`` or ``str``
        The (name of) model used to train and predict.
    optimizer: ``Optimizer`` of ``str``
        The (name of) optimizer used to train and predict.
    lr: ``float``
        The learning rate of node classification task.
    max_epoch: ``int``
        The max number of epochs in training.
    early_stopping_round: ``int``
        The round of early stop.
    device: ``torch.device`` or ``str``
        The device where model will be running on.
    init: ``bool``
        If True(False), the model will (not) be initialized.
    """

    def __init__(
        self,
        model: Union[BaseModel, str] = None,
        G=None,
        meta_paths = None, # Jie
        num_features=None,
        num_classes=None,
        optimizer=None,
        lr=None,
        max_epoch=None,
        early_stopping_round=None,
        weight_decay=1e-4,
        device="auto",
        init=True,
        feval=[Logloss],
        loss="nll_loss",
        lr_scheduler_type=None,
        *args,
        **kwargs
    ):
        super().__init__(
            model,
            G,
            meta_paths,
            num_features,
            num_classes,
            device=device,
            init=init,
            feval=feval,
            loss=loss,
        )
       
        self.opt_received = optimizer
        if type(optimizer) == str and optimizer.lower() == "adam":
            self.optimizer = torch.optim.Adam
        elif type(optimizer) == str and optimizer.lower() == "sgd":
            self.optimizer = torch.optim.SGD
        else:
            self.optimizer = torch.optim.Adam

        self.lr_scheduler_type = lr_scheduler_type

        self.lr = lr if lr is not None else 1e-4
        self.max_epoch = max_epoch if max_epoch is not None else 100
        self.early_stopping_round = (
            early_stopping_round if early_stopping_round is not None else 100
        )
        self.args = args
        self.kwargs = kwargs

        self.feval = get_feval(feval)

        self.weight_decay = weight_decay

        self.early_stopping = EarlyStopping(
            patience=early_stopping_round, verbose=False
        )

        self.valid_result = None
        self.valid_result_prob = None
        self.valid_score = None

        self.initialized = False

        self.pyg_dgl = DependentBackend.get_backend_name()

        self.space = [
            {
                "parameterName": "max_epoch",
                "type": "INTEGER",
                "maxValue": 500,
                "minValue": 10,
                "scalingType": "LINEAR",
            },
            {
                "parameterName": "early_stopping_round",
                "type": "INTEGER",
                "maxValue": 30,
                "minValue": 10,
                "scalingType": "LINEAR",
            },
            {
                "parameterName": "lr",
                "type": "DOUBLE",
                "maxValue": 1e-1,
                "minValue": 1e-4,
                "scalingType": "LOG",
            },
            {
                "parameterName": "weight_decay",
                "type": "DOUBLE",
                "maxValue": 1e-2,
                "minValue": 1e-4,
                "scalingType": "LOG",
            },
        ]

        self.hyperparams = {
            "max_epoch": self.max_epoch,
            "early_stopping_round": self.early_stopping_round,
            "lr": self.lr,
            "weight_decay": self.weight_decay,
        }

        if init is True:
            self.initialize()

    def initialize(self):
        #  Initialize the auto model in trainer.
        if self.initialized is True:
            return
        self.initialized = True
        self.model.initialize()

    def get_model(self):
        # Get auto model used in trainer.
        return self.model

    @classmethod
    def get_task_name(cls):
        # Get task name, i.e., `NodeClassification`.
        return "NodeClassificationHet"

    def train_only(self, data, G, train_mask=None):
        """
        The function of training on the given dataset and mask.
        Parameters
        ----------
        data: The node classification dataset used to be trained. It should consist of masks, including train_mask, and etc.
        train_mask: The mask used in training stage.
        Returns
        -------
        self: ``autogl.train.NodeClassificationTrainer``
            A reference of current trainer.
        """
        labels = data["labels"]
        labels = labels.to(self.device)
        val_mask = data["val_mask"]
        val_mask = val_mask.to(self.device)
        optimizer = self.optimizer(
            self.model.model.parameters(), lr=self.lr, weight_decay=self.weight_decay
        )

        lr_scheduler_type = self.lr_scheduler_type
        if type(lr_scheduler_type) == str and lr_scheduler_type == "steplr":
            scheduler = StepLR(optimizer, step_size=100, gamma=0.1)
        elif type(lr_scheduler_type) == str and lr_scheduler_type == "multisteplr":
            scheduler = MultiStepLR(optimizer, milestones=[30, 80], gamma=0.1)
        elif type(lr_scheduler_type) == str and lr_scheduler_type == "exponentiallr":
            scheduler = ExponentialLR(optimizer, gamma=0.1)
        elif (
            type(lr_scheduler_type) == str and lr_scheduler_type == "reducelronplateau"
        ):
            scheduler = ReduceLROnPlateau(optimizer, "min")
        else:
            scheduler = None

        for epoch in range(1, self.max_epoch):
            self.model.model.train()
            optimizer.zero_grad()
            if hasattr(self.model.model, 'cls_forward'):
                logits = self.model.model.cls_forward(G, 'paper')
            else:
                logits = self.model.model.forward(G, 'paper')

            # 手工定义的loss，后面需要改
            loss_fcn = torch.nn.CrossEntropyLoss()
            # print(logits[train_mask].size()) # torch.Size([1, 4025, 3])
            # print(labels[train_mask].size()) # torch.Size([1, 4025])
            loss = loss_fcn(logits[train_mask], labels[train_mask]) #!!!!!!
            # loss = loss_fcn(logits[train_mask][0], labels[train_mask][0]) #!!!!!!
            loss.backward()
            optimizer.step()
            if self.lr_scheduler_type:
                scheduler.step()

            if val_mask is not None:
                if type(self.feval) is list:
                    feval = self.feval[0]
                else:
                    feval = self.feval

                # val_loss = self.evaluate([data], mask=val_mask, feval=feval)
                val_loss = self.evaluate(G, labels, val_mask, loss_fcn)[0]
                if feval.is_higher_better() is True:
                    val_loss = -val_loss

                self.early_stopping(val_loss, self.model.model)
                if self.early_stopping.early_stop:
                    LOGGER.debug("Early stopping at %d", epoch)
                    break
        if hasattr(data, "val_mask") and data.val_mask is not None:
            self.early_stopping.load_checkpoint(self.model.model)

    def predict_only(self, G, mask=None):
        """
        The function of predicting on the given dataset and mask.
        Parameters
        ----------
        data: The node classification dataset used to be predicted.
        train_mask: The mask used in training stage.
        Returns
        -------
        res: The result of predicting on the given dataset.
        """
        self.model.model.eval()
        with torch.no_grad():
            if hasattr(self.model.model, 'cls_forward'):
                res = self.model.model.cls_forward(G, 'paper')
            else:
                res = self.model.model.forward(G, 'paper')

        if mask is None:
            return res
        else:
            return res[mask]

    def train(self, data, G, keep_valid_result=True, train_mask=None):
        """
        The function of training on the given dataset and keeping valid result.
        Parameters
        ----------
        dataset: The node classification dataset used to be trained.
        keep_valid_result: ``bool``
            If True(False), save the validation result after training.
        train_mask: The mask for training data
        Returns
        -------
        self: ``autogl.train.NodeClassificationTrainer``
            A reference of current trainer.
        """
        self.train_only(data, G, train_mask)
        if keep_valid_result:
            # generate labels
            val_mask = data["val_mask"]
            self.valid_result = self.predict_only(G)[val_mask].max(1)[1]
            self.valid_result_prob = self.predict_only(G)[val_mask]
            self.valid_score = self.evaluate(
                data, mask=val_mask, feval=self.feval
            )
            # print(self.valid_score)

    def predict(self, G, mask=None):
        """
        The function of predicting on the given dataset.
        Parameters
        ----------
        dataset: The node classification dataset used to be predicted.
        mask: ``train``, ``val``, or ``test``.
            The dataset mask.
        Returns
        -------
        The prediction result of ``predict_proba``.
        """
        return self.predict_proba(G, mask=mask, in_log_format=True).max(1)[1]

    def predict_proba(self, G, mask=None, in_log_format=False):
        """
        The function of predicting the probability on the given dataset.
        Parameters
        ----------
        dataset: The node classification dataset used to be predicted.
        mask: ``train``, ``val``, ``test``, or ``Tensor``.
            The dataset mask.
        in_log_format: ``bool``.
            If True(False), the probability will (not) be log format.
        Returns
        -------
        The prediction result.
        """
        # data = dataset[0]
        # data = data.to(self.device)
        ret = self.predict_only(G, mask)
        if in_log_format is True:
            return ret
        else:
            return torch.exp(ret)

    def get_valid_predict(self):
        # """Get the valid result."""
        return self.valid_result

    def get_valid_predict_proba(self):
        # """Get the valid result (prediction probability)."""
        return self.valid_result_prob

    def get_valid_score(self, return_major=True):
        """
        The function of getting the valid score.
        Parameters
        ----------
        return_major: ``bool``.
            If True, the return only consists of the major result.
            If False, the return consists of the all results.
        Returns
        -------
        result: The valid score in training stage.
        """
        if isinstance(self.feval, list):
            if return_major:
                return self.valid_score[0], self.feval[0].is_higher_better()
            else:
                return self.valid_score, [f.is_higher_better() for f in self.feval]
        else:
            return self.valid_score, self.feval.is_higher_better()

    def __repr__(self) -> str:
        import yaml

        return yaml.dump(
            {
                "trainer_name": self.__class__.__name__,
                "optimizer": self.optimizer,
                "learning_rate": self.lr,
                "max_epoch": self.max_epoch,
                "early_stopping_round": self.early_stopping_round,
                "model": repr(self.model),
            }
        )

    def evaluate(self, G, labels, mask=None, loss_func = None):
        """
        The function of training on the given dataset and keeping valid result.
        Parameters
        ----------
        dataset: The node classification dataset used to be evaluated.
        mask: ``train``, ``val``, or ``test``.
            The dataset mask.
        feval: ``str``.
            The evaluation method used in this function.
        Returns
        -------
        res: The evaluation result on the given dataset.
        """
        mask = mask.bool()
        self.model.model.eval()
        with torch.no_grad():
            logits = self.model.model(G, 'paper')
        loss = loss_func(logits[mask], labels[mask])
        accuracy, micro_f1, macro_f1 = score(logits[mask], labels[mask])

        return loss, accuracy, micro_f1, macro_f1

    def to(self, new_device):
        assert isinstance(new_device, torch.device)
        self.device = new_device
        if self.model is not None:
            self.model.to(self.device)

    def duplicate_from_hyper_parameter(self, hp: dict, model=None, restricted=True):
        """
        The function of duplicating a new instance from the given hyperparameter.
        Parameters
        ----------
        hp: ``dict``.
            The hyperparameter used in the new instance.
        model: The model used in the new instance of trainer.
        restricted: ``bool``.
            If False(True), the hyperparameter should (not) be updated from origin hyperparameter.
        Returns
        -------
        self: ``autogl.train.NodeClassificationTrainer``
            A new instance of trainer.
        """
        if not restricted:
            origin_hp = deepcopy(self.hyperparams)
            origin_hp.update(hp)
            hp = origin_hp
        if model is None:
            model = self.model
        model = model.from_hyper_parameter(
            dict(
                [
                    x
                    for x in hp.items()
                    if x[0] in [y["parameterName"] for y in model.space]
                ]
            )
        )

        ret = self.__class__(
            model=model,
            num_features=self.num_features,
            num_classes=self.num_classes,
            optimizer=self.opt_received,
            lr=hp["lr"],
            max_epoch=hp["max_epoch"],
            early_stopping_round=hp["early_stopping_round"],
            device=self.device,
            weight_decay=hp["weight_decay"],
            feval=self.feval,
            loss=self.loss,
            lr_scheduler_type=self.lr_scheduler_type,
            init=True,
            *self.args,
            **self.kwargs
        )

        return ret

    @property
    def hyper_parameter_space(self):
        # """Get the space of hyperparameter."""
        return self.space

    @hyper_parameter_space.setter
    def hyper_parameter_space(self, space):
        # """Set the space of hyperparameter."""
        self.space = space

    def get_hyper_parameter(self):
        # """Get the hyperparameter in this trainer."""
        return self.hyperparams