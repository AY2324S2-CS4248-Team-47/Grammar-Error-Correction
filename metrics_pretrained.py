import torch
from datasets import load_dataset, DatasetDict
from transformers import (
    AutoModelForCausalLM,
    AutoModelForSeq2SeqLM,
    AutoTokenizer,
    pipeline,
)
from peft import PeftModel, PeftConfig
import json
from huggingface_hub import login
from evaluate import load


def login_to_hf_hub():
    login(token="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")


def process_split(dataset_name, dataset_config) -> DatasetDict:
    raw_datasets = load_dataset(dataset_name, dataset_config)
    train_test_split = raw_datasets["train"].train_test_split(test_size=0.1, seed=0)
    raw_datasets["train"] = train_test_split["train"]
    raw_datasets["test"] = train_test_split["test"]
    return raw_datasets


def get_prompt(text):
    INSTRUCTION = (
        "Rewrite the given text and correct grammar, spelling, and punctuation errors."
    )
    prompt = f"### Instruction:\n{INSTRUCTION}\n### Input:\n{text}\n### Response:\n"
    return prompt


def preprocess_pretrained_model(examples):
    model_checkpoint = "google/flan-t5-large"
    tokenizer = AutoTokenizer.from_pretrained(model_checkpoint, eos_token="")
    inputs = examples["text"]
    model_inputs = tokenizer(
        inputs, max_length=512, truncation=True, return_offsets_mapping=True
    )

    labels_out = []
    offset_mapping = model_inputs.pop("offset_mapping")
    for i in range(len(model_inputs["input_ids"])):
        example_idx = i

        start_idx = offset_mapping[i][0][0]
        end_idx = offset_mapping[i][-2][1]

        edits = examples["edits"][example_idx]

        corrected_text = inputs[example_idx][start_idx:end_idx]

        for start, end, correction in reversed(
            list(zip(edits["start"], edits["end"], edits["text"]))
        ):
            if start < start_idx or end > end_idx:
                continue
            start_offset = start - start_idx
            end_offset = end - start_idx
            if correction == None:
                correction = tokenizer.unk_token
            corrected_text = (
                corrected_text[:start_offset] + correction + corrected_text[end_offset:]
            )

        labels_out.append(corrected_text)

    prompts = [get_prompt(text) for text in examples["text"]]
    return {"inputs": prompts, "labels": labels_out}


def run_pipeline_and_evaluate():
    raw_dataset = process_split("wi_locness", "wi")

    model_path = (
        # "AY2324S2-CS4248-Team-47/StableLM-WI_Locness"
        "AY2324S2-CS4248-Team-47/Phi2-WI_Locness"
    )

    config = PeftConfig.from_pretrained(model_path)
    model = AutoModelForCausalLM.from_pretrained(
        config.base_model_name_or_path,
        load_in_8bit=True,
        attn_implementation="flash_attention_2",
    )
    tokenizer = AutoTokenizer.from_pretrained(
        config.base_model_name_or_path, use_fast=True
    )
    if tokenizer.pad_token is None:
        tokenizer.add_special_tokens({"pad_token": "[PAD]", "unk_token": "[UNK]"})
        model.resize_token_embeddings(len(tokenizer))
    tokenizer.padding_side = "left"
    model = PeftModel.from_pretrained(model, model_path)
    model = model.merge_and_unload(safe_merge=True)

    pipe = pipeline(
        "text-generation",
        model=model,
        tokenizer=tokenizer,
        device_map="auto",
        torch_dtype=torch.float16,
    )

    print(f"model loaded: {model_path}")

    test_dataset = raw_dataset.map(
        preprocess_pretrained_model,
        batched=True,
        remove_columns=raw_dataset["train"].column_names,
    )["test"]

    test_inputs = test_dataset["inputs"]
    test_references = test_dataset["labels"]
    nested_test_references = [[ref] for ref in test_references]

    predictions = pipe(test_inputs, max_new_tokens=512, num_return_sequences=1)
    predictions = [
        pred[0]["generated_text"].split("### Response:\n")[1].strip()
        for pred in predictions
    ]

    print("Preds and refs ready!")

    bleu = load("bleu")
    rouge = load("rouge")
    bertscore = load("bertscore")

    bleu_score = bleu.compute(
        predictions=predictions, references=nested_test_references
    )
    rouge_score = rouge.compute(predictions=predictions, references=test_references)
    bert_score = bertscore.compute(
        predictions=predictions, references=test_references, lang="en"
    )

    print(bleu_score)
    print(rouge_score)
    print(bert_score)

    print(f"\nInput text: \n{test_inputs[1]}\n")
    print(f"Reference corrected text: \n{test_references[1]}\n")
    print(f"Model output: \n{predictions[1]}\n")

    data = {
        "inputs": test_inputs,
        "predictions": predictions,
        "references": test_references,
    }
    with open("data.json", "w") as f:
        json.dump(data, f)

    metrics = {"bleu": bleu_score, "rouge": rouge_score, "bertscore": bert_score}
    with open("metrics.txt", "w") as f:
        f.write(json.dumps(metrics))


if __name__ == "__main__":
    login_to_hf_hub()
    run_pipeline_and_evaluate()
