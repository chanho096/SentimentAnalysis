import kobert.pytorch_kobert
import kobert.utils

import torch
import gluonnlp as nlp
from gluonnlp.data import TSVDataset
import numpy as np

DEFAULT_OPTION = {
    "batch_size": 64,
    "num_epochs": 5,

    # Pre-Processing
    "max_len": 64,

    # Training
    "learning_rate": 5e-5,
    "drop_out_rate": 0.5,

    # AdamW
    "warmup_ratio": 0.2,
    "max_grad_norm": 1,

    # Print
    "log_interval": 100
}


class BERTDataset(torch.utils.data.Dataset):
    def __init__(self, dataset, sentence_idx, label_idx, bert_tokenizer, max_len, pad, pair):
        # Tokenization 수행
        transform = nlp.data.BERTSentenceTransform(
            bert_tokenizer, max_seq_length=max_len, pad=pad, pair=pair)
        self.sentence = [transform([record[sentence_idx]]) for record in dataset]
        self.labels = [np.int32(record[label_idx]) for record in dataset]

    def __getitem__(self, i):
        return self.sentence[i] + (self.labels[i],)

    def __len__(self):
        return len(self.labels)


class BERTClassifier(torch.nn.Module):
    def __init__(self,
                 bert,
                 hidden_size=768,
                 num_classes=2,
                 dr_rate=None,
                 ):
        super(BERTClassifier, self).__init__()
        self.bert = bert
        self.num_classes = num_classes
        self.dr_rate = dr_rate

        self.classifier = torch.nn.Linear(hidden_size, num_classes)

        if dr_rate:
            self.dropout = torch.nn.Dropout(p=dr_rate)

    def forward(self, x, segment_ids, attention_mask):
        # bert forward
        _, pooler = self.bert(inputs_embeds=x, token_type_ids=segment_ids.long(),
                              attention_mask=attention_mask)

        # drop-out layer
        out = self.dropout(pooler) if self.dr_rate else pooler

        # softmax output
        out = self.classifier(out)
        out = torch.nn.functional.softmax(out, dim=1)

        return out


class ABSAClassifier(torch.nn.Module):
    def __init__(self,
                 bert,
                 hidden_size=768,
                 num_classes=2,
                 dr_rate=None,
                 ):
        super(ABSAClassifier, self).__init__()
        self.bert = bert
        self.num_classes = num_classes
        self.dr_rate = dr_rate

        self.classifier_1 = torch.nn.Linear(hidden_size, num_classes)
        self.classifier_2 = torch.nn.Linear(hidden_size, num_classes)

        if dr_rate:
            self.dropout_1 = torch.nn.Dropout(p=dr_rate)
            self.dropout_2 = torch.nn.Dropout(p=dr_rate)

    def forward(self, x, segment_ids, attention_mask):
        # bert forward
        with torch.no_grad():
            _, pooler = self.bert(inputs_embeds=x, token_type_ids=segment_ids.long(),
                                  attention_mask=attention_mask)

        # drop-out layer
        out_1 = self.dropout_1(pooler) if self.dr_rate else pooler

        # softmax output
        out_1 = self.classifier_1(out_1)
        out_1 = torch.nn.functional.softmax(out_1, dim=1)

        return out_1


def calculate_accuracy(x, y):
    max_vals, max_indices = torch.max(x, 1)
    train_acc = (max_indices == y).sum().data.cpu().numpy() / max_indices.size()[0]
    return train_acc


def get_bert_tokenizer(vocab):
    tokenizer = kobert.utils.get_tokenizer()
    bert_tokenizer = nlp.data.BERTSPTokenizer(tokenizer, vocab, lower=False)

    return bert_tokenizer


def get_bert_dataset(corpus_path, sentence_idx, label_idx, max_len, vocab=None, bert_tokenizer=None):
    if not vocab and not bert_tokenizer:
        # vocab or bert_tokenizer must be required
        return None

    # load train / test dataset
    dataset = nlp.data.TSVDataset(corpus_path, field_indices=[sentence_idx, label_idx], num_discard_samples=1)

    # text data pre-processing
    if bert_tokenizer is None:
        bert_tokenizer = get_bert_tokenizer(vocab)
    bert_dataset = BERTDataset(dataset, 0, 1, bert_tokenizer, max_len, pad=True, pair=False)

    return bert_dataset


def gen_attention_mask(token_ids, valid_length):
    attention_mask = torch.zeros_like(token_ids)
    for i, v in enumerate(valid_length):
        attention_mask[i][:v] = 1
    return attention_mask.float()


def sentiment_analysis(model_path, corpus_path, sentence_idx, label_idx, opt=DEFAULT_OPTION, ctx="cuda:0", show=False):
    device = torch.device(ctx)

    # load bert model
    bert_model, vocab = kobert.pytorch_kobert.get_pytorch_kobert_model()

    # data pre-processing
    bert_dataset = get_bert_dataset(corpus_path, sentence_idx=sentence_idx, label_idx=label_idx,
                                    max_len=opt["max_len"], vocab=vocab)

    # data loader
    dataloader = torch.utils.data.DataLoader(bert_dataset, batch_size=opt["batch_size"], num_workers=0)

    # load model
    model = BERTClassifier(bert_model).to(device)
    model.load_state_dict(torch.load(model_path))

    # evaluate
    result = np.zeros((len(bert_dataset), 2), dtype=np.float32)
    accuracy = 0.0
    si = 0

    model.eval()
    with torch.no_grad():
        for batch_id, (token_ids, valid_length, segment_ids, label) in enumerate(dataloader):
            # set test batch
            token_ids = token_ids.long().to(device)
            segment_ids = segment_ids.long().to(device)
            valid_length = valid_length
            label = label.long().to(device)

            # get word embedding
            attention_mask = gen_attention_mask(token_ids, valid_length)
            word_embedding = model.bert.get_input_embeddings()
            x = word_embedding(token_ids)

            # forward propagation
            out = model(x, segment_ids, attention_mask)

            # test accuracy
            accuracy += calculate_accuracy(out, label)

            ei = si + label.size()[0]
            result[si:ei, :] = out.cpu().numpy()
            si = ei

            if show and batch_id % opt["log_interval"] == 0:
                print("Predict {}%".format(round(si / len(bert_dataset) * 100, 2)))
        accuracy = accuracy / (batch_id + 1)

    return result, accuracy


