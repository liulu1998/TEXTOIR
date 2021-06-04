from importlib import import_module
import logging
import torch
import torch.nn.functional as F
import numpy as np
import os
import copy
from torch import nn
from datetime import datetime
from sklearn.metrics import confusion_matrix, accuracy_score
from tqdm import trange, tqdm

from losses import loss_map
from utils.metrics import F_measure

TIMESTAMP = "{0:%Y-%m-%dT%H-%M-%S/}".format(datetime.now())
train_log_dir = 'logs/train/' + TIMESTAMP
test_log_dir = 'logs/test/'   + TIMESTAMP

        
class MSPManager:
    
    def __init__(self, args, data, model):

        self.logger = logging.getLogger('Detection')

        self.model = model.model 
        self.optimizer = model.optimizer
        self.device = model.device

        self.data = data 
        self.train_dataloader = data.dataloader.train_labeled_loader
        self.eval_dataloader = data.dataloader.eval_loader 
        self.test_dataloader = data.dataloader.test_loader

        self.loss_fct = loss_map[args.loss_fct]
        
        if not args.train:

            model_file = os.path.join(args.model_output_dir, 'pytorch_model.bin')
            self.model.load_state_dict(torch.load(model_file))
            self.model.to(self.device)


    def get_outputs(self, args, data, dataloader, get_feats = False):

        self.model.eval()

        total_labels = torch.empty(0,dtype=torch.long).to(self.device)
        total_logits = torch.empty((0, data.num_labels)).to(self.device)
        total_features = torch.empty((0,args.feat_dim)).to(self.device)

        for batch in tqdm(dataloader, desc="Iteration"):

            batch = tuple(t.to(self.device) for t in batch)
            input_ids, input_mask, segment_ids, label_ids = batch
            with torch.set_grad_enabled(False):

                pooled_output, logits = self.model(input_ids, segment_ids, input_mask)

                total_labels = torch.cat((total_labels,label_ids))
                total_logits = torch.cat((total_logits, logits))
                total_features = torch.cat((total_features, pooled_output))

        if get_feats:  
            feats = total_features.cpu().numpy()
            return feats 

        else:
            
            total_probs = F.softmax(total_logits.detach(), dim=1)
            total_maxprobs, total_preds = total_probs.max(dim = 1)
            y_prob = total_maxprobs.cpu().numpy()

            y_true = total_labels.cpu().numpy()

            y_pred = total_preds.cpu().numpy()
            y_pred[y_prob < args.threshold] = data.unseen_label_id

        return y_true, y_pred

    def test(self, args, data, show=False):

        y_true, y_pred = self.get_outputs(args, data, self.test_dataloader)
        cm = confusion_matrix(y_true, y_pred)
        test_results = F_measure(cm)

        acc = round(accuracy_score(y_true, y_pred) * 100, 2)
        test_results['Acc'] = acc
        
        if show:
            self.logger.info(f'cm {cm}')
            self.logger.info(f'results {test_results}')

        return test_results


    def train(self, args, data):     
        
        best_model = None
        best_eval_score = 0

        wait = 0
        for epoch in trange(int(args.num_train_epochs), desc="Epoch"):

            self.model.train()
            tr_loss = 0
            nb_tr_examples, nb_tr_steps = 0, 0
            
            for step, batch in enumerate(tqdm(self.train_dataloader, desc="Iteration")):
                batch = tuple(t.to(self.device) for t in batch)
                input_ids, input_mask, segment_ids, label_ids = batch
                with torch.set_grad_enabled(True):
                    
                    loss = self.model(input_ids, segment_ids, input_mask, label_ids, mode='train', loss_fct=self.loss_fct)

                    self.optimizer.zero_grad()
                    loss.backward()
                    self.optimizer.step()
                    
                    tr_loss += loss.item()
                    
                    nb_tr_examples += input_ids.size(0)
                    nb_tr_steps += 1

            loss = tr_loss / nb_tr_steps
            self.logger.info(f'train_loss {loss}')
            
            y_true, y_pred = self.get_outputs(args, data, self.eval_dataloader)
            eval_score = accuracy_score(y_true, y_pred)
            self.logger.info(f'eval_score {eval_score}')
            
            
            if eval_score > best_eval_score:
                wait = 0
                best_model = copy.deepcopy(self.model)
                best_eval_score = eval_score 

            elif eval_score > 0:

                wait += 1
                if wait >= args.wait_patient:
                    break
    
        self.model = best_model 

        if args.save_model:
            self.model.save_pretrained(args.model_output_dir, save_config=True)


    



  

    
    
