import numpy as np
import pandas as pd
import torch
import os
import random
import math
import torch.nn as nn
import transformers

from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report
from transformers import AutoTokenizer, AutoModel
from transformers import AdamW, get_polynomial_decay_schedule_with_warmup

from sklearn.metrics import accuracy_score
from sklearn.metrics import classification_report
from sklearn.metrics import confusion_matrix

device = torch.device('cuda') if torch.cuda.is_available() else torch.device('cpu')
print(device)

seed_val = 0
random.seed(seed_val)
np.random.seed(seed_val)
torch.manual_seed(seed_val)
torch.cuda.manual_seed_all(seed_val)
torch.backends.cudnn.benchmark = False
#torch.use_deterministic_algorithms(True)
torch.backends.cudnn.deterministic = True

"""# Load Dataset"""

df_train = pd.read_csv("training_data.csv")
df_val = pd.read_csv("trial_data.csv")

df_train = df_train[(df_train['section']!='others')]
df_val = df_val[(df_val['section']!='others')]

df_train['section'] = df_train['section'].replace({'experiment' : 'result'}, regex=True)
df_val['section'] = df_val['section'].replace({'experiment' : 'result'}, regex=True)

df_section = pd.read_csv("section_dataset.csv")
df_citation = pd.read_csv("citation_scaffold.csv")

df_section = df_section[(df_section['section']!='discussion')]
df_citation['citation']=df_citation['is_citation'].replace({False: 0, True: 1})

label_dict = {'result': 0, 'background': 1, 'method': 2, 'introduction': 3, 'abstract': 4, 'title': 5}

"""# Import BERT Model and BERT Tokenizer"""

tokenizer = AutoTokenizer.from_pretrained('scibert_scivocab_uncased')
bert = AutoModel.from_pretrained('scibert_scivocab_uncased')

"""#Encoding Labels"""

print(label_dict)

df_train['section_label']=df_train['section'].replace(label_dict)
df_val['section_label']=df_val['section'].replace(label_dict)
df_section['section_label']=df_section['section'].replace(label_dict)

length = len(label_dict)

section_text = df_section['text']
section_label = df_section['section_label']

citation_text = df_citation['text']
citation_label = df_citation['citation']

print(len(df_train.index))
print(len(df_val.index))

"""#Tokenization"""

training_text = df_train['text'] + " # " + df_train['sub_heading'] +" # " + df_train['prev_text'] + " # " + df_train['next_text']
validating_text = df_val['text'] + " # " + df_val['sub_heading'] +" # " + df_val['prev_text'] + " # " + df_val['next_text']
training_section = df_train['section_label']
validating_section = df_val['section_label']
training_labels = df_train['label']
validating_labels = df_val['label']
training_citation = df_train['citation']
validating_citation = df_val['citation']

max_seq = 256

# tokenize and encode sequences in the training set
train_text = tokenizer.batch_encode_plus(training_text.tolist(), padding='max_length', max_length = max_seq, truncation = True,  return_token_type_ids=False)

# tokenize and encode sequences in the val set
val_text = tokenizer.batch_encode_plus(validating_text.tolist(), padding='max_length', max_length = max_seq, truncation = True, return_token_type_ids=False)

# tokenize and encode sequences in the training set
scaffold_section = tokenizer.batch_encode_plus(section_text.tolist(), padding='max_length', max_length = max_seq, truncation = True,  return_token_type_ids=False)

# tokenize and encode sequences in the training set
scaffold_citation = tokenizer.batch_encode_plus(citation_text.tolist(), padding='max_length', max_length = max_seq, truncation = True,  return_token_type_ids=False)


"""# Convert Integer Sequences to Tensors"""

# for train set
train_input = torch.tensor(train_text['input_ids'])
train_attention = torch.tensor(train_text['attention_mask'])
train_label = torch.tensor(training_labels.tolist())
train_section = torch.tensor(training_section.tolist())
train_citation = torch.tensor(training_citation.tolist())

# for val set
val_input = torch.tensor(val_text['input_ids'])
val_attention = torch.tensor(val_text['attention_mask'])
val_label = torch.tensor(validating_labels.tolist())
val_section = torch.tensor(validating_section.tolist())
val_citation = torch.tensor(validating_citation.tolist())

# for scaffold set
section_sn = torch.tensor(scaffold_section['input_ids'])
section_sm = torch.tensor(scaffold_section['attention_mask'])
section_y = torch.tensor(section_label.tolist())

# for scaffold set
citation_sn = torch.tensor(scaffold_citation['input_ids'])
citation_sm = torch.tensor(scaffold_citation['attention_mask'])
citation_y = torch.tensor(citation_label.tolist())

"""# Create DataLoaders"""

from torch.utils.data import TensorDataset, DataLoader, RandomSampler, SequentialSampler
#define a batch size
batch_size = 16

# wrap tensors
train_data = TensorDataset(train_input, train_attention, train_label, train_section, train_citation)
# dataLoader for train set
train_dataloader = DataLoader(train_data, batch_size=batch_size, shuffle = True)

# wrap tensors
val_data = TensorDataset(val_input, val_attention, val_label, val_section, val_citation)
# dataLoader for val set
val_dataloader = DataLoader(val_data, batch_size=batch_size, shuffle = True)

# wrap tensors
section_data = TensorDataset(section_sn, section_sm, section_y)
# dataLoader for scaffold set
section_dataloader = DataLoader(section_data, batch_size=batch_size, shuffle = True)

# wrap tensors
citation_data = TensorDataset(citation_sn, citation_sm, citation_y)
# dataLoader for scaffold set
citation_dataloader = DataLoader(citation_data, batch_size=batch_size, shuffle = True)


"""#Define Model Architecture"""

class SciBERT_Classifier(nn.Module):

    def __init__(self, bert, config):
      
      super(SciBERT_Classifier, self).__init__()

      self.bert = bert
      self.drop = nn.Dropout(config.dropout)
      
      #Main Task
      self.fc1 = nn.Linear(768, 768)
      self.fc2 = nn.Linear(768, 2)
      
      #Section Identifcation
      self.fc4 = nn.Linear(768, 768)
      self.fc5 = nn.Linear(768, length)
      
      #Citation Worthiness
      self.fc6 = nn.Linear(768, 768)
      self.fc7 = nn.Linear(768, 2)
      
      #Activation Function
      self.act = nn.Tanh()

    #define the forward pass
    def forward(self, batch, x):

      #pass the inputs to the model  
      output1 = self.bert(batch[0], attention_mask=batch[1])
      pooled_output1 = output1[1]
      pooled_output1 = self.drop(pooled_output1)
      
      #Main Task
      if(x == 0):
      	output1 = self.fc1(pooled_output1)
      	output1 = self.act(output1)
      	output1 = self.drop(output1)
      	output1 = self.fc2(output1)
      
      #Section Identifcation
      if(x == 1 or x == 0):
      	output2 = self.fc4(pooled_output1)
      	output2 = self.act(output2)
      	output2 = self.drop(output2)
      	output2 = self.fc5(output2)

      #Citation Worthiness
      
      if(x == 2 or x == 0):
      	output3 = self.fc6(pooled_output1)
      	output3 = self.act(output3)
      	output3 = self.drop(output3)
      	output3 = self.fc7(output3)
      
      if(x == 0):
      	return output1, output2, output3
      if(x == 1):
      	return output2, output2
      if(x == 2):
      	return output3, output3
'''
# pass the pre-trained BERT to our define architecture
model = SciBERT_Classifier(bert)
# push the model to GPU
model = model.to(device)

from transformers import AdamW, get_linear_schedule_with_warmup, get_polynomial_decay_schedule_with_warmup

optimizer = AdamW(model.parameters(), lr=2e-5,  eps=1e-8)
epochs = 3
scheduler = get_linear_schedule_with_warmup(optimizer, num_warmup_steps=0, num_training_steps=(len(train_dataloader)+len(section_dataloader)+len(citation_dataloader))*epochs)
'''

from sklearn.utils import class_weight
citation_weights = class_weight.compute_class_weight('balanced', np.unique(citation_label), citation_label)
citation_weights = torch.tensor(citation_weights, dtype=torch.float32).to(device)
citation_weights = citation_weights/citation_weights.sum()

print(citation_weights)

criterion2 = torch.nn.CrossEntropyLoss()
criterion2 = criterion2.to(device)
criterion3 = torch.nn.CrossEntropyLoss(weight = citation_weights)
criterion3 = criterion3.to(device)


"""# Training LSTM on BERT Embeddings

"""

# function to train the model
def train(model, config, optimizer, scheduler, criterion1):
  
  model.train()

  main_loss, section_loss, citation_loss, total_accuracy = 0, 0, 0, 0, 0
  
  predictions = []
  label = []
  
  section_iter = iter(section_dataloader)
  citation_iter = iter(citation_dataloader)

  # iterate over batches
  for step, batch in enumerate(train_dataloader):
    
    # progress update after every 50 batches.
    if step % 30 == 0 and not step == 0:
      print('  Batch {:>5,}  of  {:>5,}.'.format(step, len(train_dataloader)))
      
    if(step%3==0):
    	for i in range(5):
    		scaffold = next(section_iter)

    		scaffold = [r.to(device) for r in scaffold]
    		sections = scaffold[2]

    		# clear previously calculated gradients 
    		model.zero_grad()

    		output, _ = model(scaffold, 1)

    		loss = criterion2(output, sections)*config.lambda1

    		# backward pass to calculate the gradients
    		loss.backward()

    		section_loss = section_loss + loss.item()/config.lambda1

    		# clip the the gradients to 1.0. It helps in preventing the exploding gradient problem
    		torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)

    		# update parameters
    		optimizer.step()
    		scheduler.step()
    
    if(step%3==0):
    	for i in range(4):
    		scaffold = next(citation_iter)

    		scaffold = [r.to(device) for r in scaffold]
    		citation = scaffold[2]

    		# clear previously calculated gradients 
    		model.zero_grad()

    		output, _ = model(scaffold, 2)

    		loss = criterion3(output, citation)*config.lambda2

    		# backward pass to calculate the gradients
    		loss.backward()

    		citation_loss = citation_loss + loss.item()/config.lambda2

    		# clip the the gradients to 1.0. It helps in preventing the exploding gradient problem
    		torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)

    		# update parameters
    		optimizer.step()
    		scheduler.step()

    # push the batch to gpu
    batch = [r.to(device) for r in batch]
    labels = batch[2]
    sections = batch[3]
    citations = batch[4]

    # clear previously calculated gradients 
    model.zero_grad()        

    # get model predictions for the current batch
    output1, output2, output3 = model(batch, 0)	
    
    # compute the loss between actual and predicted values
    
    loss1 = criterion1(output1, labels)

    loss2 = criterion2(output2, sections)

    loss3 = criterion3(output3, citations)
    
    loss = loss1 + loss2*config.lambda1 + loss3*config.lambda2

    # backward pass to calculate the gradients
    loss.backward()

    main_loss = main_loss + loss1.item()
    section_loss = section_loss + loss2.item()
    citation_loss = citation_loss + loss3.item())

    # clip the the gradients to 1.0. It helps in preventing the exploding gradient problem
    torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)

    # update parameters
    optimizer.step()
    scheduler.step()
    
    output1 = torch.argmax(output1, axis = 1)
    output1 = output1.detach().cpu().numpy()
    labels = labels.detach().cpu().numpy()
    predictions.extend(output1)
    label.extend(labels)

  # compute the training loss of the epoch
  avg_main_loss = main_loss / len(train_dataloader)
  avg_section_loss = section_loss / (len(section_dataloader) + len(train_dataloader))
  avg_citation_loss = citation_loss / (len(citation_dataloader) + len(train_dataloader))
  
  #returns the loss and predictions
  return avg_main_loss, avg_section_loss, avg_citation_loss, predictions, label

# function for evaluating the model
def evaluate(model, config, val_dataloader, criterion1):
  
  print("\nEvaluating...")
  
  # deactivate dropout layers
  model.eval()
  main_loss, section_loss, citation_loss, total_accuracy = 0, 0, 0, 0, 0
  
  # empty list to save the model predictions
  predictions = []
  label = []
  
  # iterate over batches
  for step,batch in enumerate(val_dataloader):
    
    # Progress update every 50 batches.
    if step % 50 == 0 and not step == 0: 
      # Report progress.
      print('  Batch {:>5,}  of  {:>5,}.'.format(step, len(val_dataloader)))

    # push the batch to gpu
    batch = [r.to(device) for r in batch]
    labels = batch[2]
    sections = batch[3]
    citations = batch[4]
    
    # deactivate autograd
    with torch.no_grad():
      
      # model predictions
      output1, output2, output3 = model(batch, 0)
      
      # compute the validation loss between actual and predicted values
      
      loss1 = criterion1(output1, labels)

      loss2 = criterion2(output2, sections)

      loss3 = criterion3(output3, citations)
      

      loss = loss1 + loss2*config.lambda1 + loss3*config.lambda2

      main_loss = main_loss + loss1.item()
      section_loss = section_loss + loss2.item()
      citation_loss = citation_loss + loss3.item()

      output1 = torch.argmax(output1, axis = 1)
      output1 = output1.detach().cpu().numpy()
      labels = labels.detach().cpu().numpy()
      predictions.extend(output1)
      label.extend(labels)

  # compute the training loss of the epoch
  avg_main_loss = main_loss / len(val_dataloader)
  avg_section_loss = section_loss / len(val_dataloader)
  avg_citation_loss = citation_loss / len(val_dataloader)
  
  #returns the loss and predictions
  return avg_main_loss, avg_section_loss, avg_citation_loss, predictions, label


def custom_f1_score(predictions, labels):
  tn, fp, fn, tp = 0,0,0,0
  for i in range(len(labels)):
    if (labels[i]==6):
      continue
    if (labels[i]==1 and predictions[i]==1):
      tp += 1
    elif (labels[i] == 1):
      fn += 1
    elif (predictions[i] == 1):
      fp += 1
    else:
      tn += 1
  precision = tp/(tp+fp)
  recall = tp/(tp+fn)
  f1_score = 2*precision*recall/(precision+recall)
  return precision, recall, f1_score

from sklearn.metrics import f1_score

#loss_weights = list(np.linspace(0.01, 0.3, 30))

#print(loss_weights)

import wandb
wandb.login()

sweep_config = {
    'method': 'grid',
    'metric': {
      'name': 'Validation F1 Score',
      'goal': 'maximize'   
    },
    'parameters': {
        'epochs': {
            'values': [2]
        },
        'weights': {
            'values': [0.65, 0.68, 0.71, 0.74, 0.77, 0.80]       
        },
        'dropout': {
            'values': [0.1, 0.2]
        },
        'learning_bert_rate': {
            "values" : [5e-6, 1e-5]
        },
        'lambda1': {
            "values" : [0.18]
        },
        'lambda2': {
            "values" : [0.03]
        },
    }
}

sweep_id = wandb.sweep(sweep_config, project="Multitasking_Loss_weights")


def training():
  config_defaults = {					#Defaults set as parameters giving best results
        'epochs': 2,
        'dropout' : 0.1,
        'weights' : 0.75,
        'learning_bert_rate': 1e-5,
        'lambda1' : 0.18,
        'lambda2' : 0.09
  }
  best_val_F1 = 0
  # Initialize a new wandb run
  wandb.init(config=config_defaults)
    
  # Config is a variable that holds and saves hyperparameters and inputs
  config = wandb.config
  # pass the pre-trained BERT to our define architecture
  model = SciBERT_Classifier(bert, config)
  # push the model to GPU
  model = model.to(device)
  #Save Random Weights
  #torch.save(model.state_dict(), 'tanh_initializer.pt')
  # Initialize with Same Random Weights
  #model.load_state_dict(torch.load('tanh_initializer.pt'))
  
  epochs = config.epochs

  pretrained = model.bert.parameters()
  # Get names of pretrained parameters (including `bert.` prefix)
  pretrained_names = [f'bert.{k}' for (k, v) in model.bert.named_parameters()]
  new_params= [v for k, v in model.named_parameters() if k not in pretrained_names]
  optimizer = AdamW([{'params': pretrained}, {'params': new_params, 'lr': config.learning_bert_rate*10}],lr=config.learning_bert_rate)
  scheduler = get_polynomial_decay_schedule_with_warmup(optimizer, num_warmup_steps=0, num_training_steps=(len(train_dataloader)+len(section_dataloader)+len(citation_dataloader))*epochs, power = 0.5)
  
  class_weights = torch.tensor([1-config.weights, config.weights]).to(device)
  criterion1 = torch.nn.CrossEntropyLoss(weight = class_weights)
  criterion1 = criterion1.to(device)

  for epoch in range(epochs):
        
    print('\n Epoch {:} / {:}'.format(epoch + 1, epochs))
    
    #train model
    train_loss, section_loss, citation_loss, predictions, label = train(model, config, optimizer, scheduler, criterion1)
    train_pre, train_rec, train_f1 = custom_f1_score(predictions, label)
    print(f'\nTraining: Main Loss: {train_loss:.5f}, Section Loss : {section_loss:.5f}, Citation Loss : {citation_loss:.5f}')
    print((f'Precision: {train_pre}, Recall: {train_rec}, F1 Score: {train_f1}'))
    
    #evaluate model
    val_loss, section_loss, citation_loss, predictions, label = evaluate(model, config, val_dataloader, criterion1)
    val_pre, val_rec, val_f1 = custom_f1_score(predictions, label)
    print(f'\nValidation: Main Loss: {val_loss:.5f}, Section Loss : {section_loss:.5f}, Citation Loss : {citation_loss:.5f}')
    print((f'Precision: {val_pre}, Recall: {val_rec}, F1 Score: {val_f1}'))

    #Saving the best model
    if(best_val_F1<val_f1):
      best_val_F1 = val_f1
      torch.save({'epoch': epoch, 'model_state_dict': model.state_dict(), 'optimizer_state_dict': optimizer.state_dict()}, os.path.join(wandb.run.dir, "model.pt"))
    
    wandb.log({"training loss":train_loss})
    wandb.log({"validation loss":val_loss})
    wandb.log({"Training F1 Score":train_f1})
    wandb.log({"Validation Precision":val_pre})
    wandb.log({"Validation Recall":val_rec})
    wandb.log({"Validation F1 Score":val_f1})
    
training()						#Training on default parameters
#wandb.agent(sweep_id, training, count=24)		#for hyperparameter sweep
