import kobert.pytorch_kobert
import kobert.utils
import loader

import torch
import transformers
import gluonnlp as nlp
from gluonnlp.data import TSVDataset
import numpy as np

DEFAULT_OPTION = {
    "batch_size": 4,
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


def gen_attention_mask(token_ids, valid_length):
    attention_mask = torch.zeros_like(token_ids)
    for i, v in enumerate(valid_length):
        attention_mask[i][:v] = 1
    return attention_mask.float()


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


def _calc_accuracy(x, y):
    max_vals, max_indices = torch.max(x, 1)
    train_acc = (max_indices == y).sum().data.cpu().numpy() / max_indices.size()[0]
    return train_acc


def sentiment_analysis(opt=DEFAULT_OPTION):
    device = torch.device("cuda:0")
    torch.cuda.set_device(device)

    # load bert model
    bert_model, vocab = kobert.pytorch_kobert.get_pytorch_kobert_model()

    # load train / test dataset
    train_data_path, test_data_path = loader.download_corpus_data()
    dataset_train = nlp.data.TSVDataset(train_data_path, field_indices=[1, 2], num_discard_samples=1)
    dataset_test = nlp.data.TSVDataset(test_data_path, field_indices=[1, 2], num_discard_samples=1)

    # text data pre-processing
    tokenizer = kobert.utils.get_tokenizer()
    bert_tokenizer = nlp.data.BERTSPTokenizer(tokenizer, vocab, lower=False)

    data_train = BERTDataset(dataset_train, 0, 1, bert_tokenizer, opt["max_len"], pad=True, pair=False)
    data_test = BERTDataset(dataset_test, 0, 1, bert_tokenizer, opt["max_len"], pad=True, pair=False)

    # data loader
    train_dataloader = torch.utils.data.DataLoader(data_train, batch_size=opt["batch_size"], num_workers=0)
    test_dataloader = torch.utils.data.DataLoader(data_test, batch_size=opt["batch_size"], num_workers=0)

    # model
    model = BERTClassifier(bert_model, dr_rate=opt["drop_out_rate"]).to(device)

    no_decay = ['bias', 'LayerNorm.weight']
    optimizer_grouped_parameters = [
        {'params': [p for n, p in model.named_parameters() if not any(nd in n for nd in no_decay)],
         'weight_decay': 0.01},
        {'params': [p for n, p in model.named_parameters() if any(nd in n for nd in no_decay)], 'weight_decay': 0.0}
    ]
    optimizer = transformers.AdamW(optimizer_grouped_parameters, lr=opt["learning_rate"])
    loss_function = torch.nn.CrossEntropyLoss()

    t_total = len(train_dataloader) * opt["num_epochs"]
    warmup_steps = int(t_total * opt["warmup_ratio"])
    scheduler = transformers.optimization.get_linear_schedule_with_warmup(optimizer, warmup_steps, t_total)

    for e in range(opt["num_epochs"]):
        train_accuracy = 0.0
        test_accuracy = 0.0

        # Train Batch
        model.train()
        for batch_id, (token_ids, valid_length, segment_ids, label) in enumerate(train_dataloader):
            optimizer.zero_grad()

            # set train batch
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

            # backward propagation
            x.retain_grad()
            loss = loss_function(out, label)
            loss.backward()

            # optimization
            torch.nn.utils.clip_grad_norm_(model.parameters(), opt["max_grad_norm"])
            optimizer.step()
            scheduler.step()  # Update learning rate schedule
            train_accuracy += _calc_accuracy(out, label)

            if batch_id % opt["log_interval"] == 0:
                print("epoch {} batch id {} loss {} train accuracy {}".format(e + 1, batch_id + 1,
                                                                              loss.data.cpu().numpy(),
                                                                              train_accuracy / (batch_id + 1)))
        print("epoch {} train accuracy {}".format(e + 1, train_accuracy / (batch_id + 1)))

        # Test Batch
        model.eval()
        with torch.no_grad():
            for batch_id, (token_ids, valid_length, segment_ids, label) in enumerate(test_dataloader):
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
                test_accuracy += _calc_accuracy(out, label)
            print("epoch {} test accuracy {}".format(e + 1, test_accuracy / (batch_id + 1)))

        torch.save(model.state_dict(), "model.pt")


if __name__ == '__main__':
    sentiment_analysis()
