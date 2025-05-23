import os
import json
import time
import traceback
import openai
import pandas as pd
import argparse
from tqdm import tqdm


tqdm.pandas()

parser = argparse.ArgumentParser()
parser.add_argument("--model", type=str, default="./eurus-7b-kto-hf")
parser.add_argument('--data_filepath', type=str, default="test_prompts.json")
parser.add_argument('--output_filepath', type=str, default="./res.jsonl")
parser.add_argument("--model_type", type=str, default='mistral')
parser.add_argument('--is_cot', action='store_true')
parser.add_argument('--n_processes', type=int, default=8)
args = parser.parse_args()


assert args.data_filepath.endswith('.json')

df = pd.read_json(args.data_filepath, lines=True, orient='records')
print(f"Loaded {len(df)} examples.")

from vllm import LLM, SamplingParams
import torch
def generate_sample_batch(question_list):
    llm = LLM(
        model=args.model,
        trust_remote_code=True,
        tensor_parallel_size=torch.cuda.device_count(),
    )
    sampling_params = SamplingParams(max_tokens=2048,
                                    temperature=0.0,
                                    n=1,
                                    stop=["\nQ:"],)
    
    prefix = "Follow the given examples and answer the question.\n\n"
    prompt_list = [prefix + q for q in question_list]
    outputs = llm.generate(prompt_list, sampling_params, use_tqdm=False)
    completions = [output.outputs[0].text.strip() for output in outputs]
    return completions

from fastchat.conversation import get_conv_template
def make_conv(prompt, model_type):
    conv = get_conv_template(model_type).copy() # only mistral currently
    prompt_list = prompt.split("\n\n") # [instruction, (Q\nA), (Q\nA)]
    instruction = prompt_list.pop(0)
    conv.system_message += instruction
    prompt_list = [prompt.split("\n") for prompt in prompt_list]
    
    for sample in prompt_list:
        q = "\n".join(sample[:-1])
        a = sample[-1]
        conv.append_message(conv.roles[0], q)
        conv.append_message(conv.roles[1], a)
    
    return conv.get_prompt()

df["prompt"] = df.apply(lambda row: make_conv(row["text"], args.model_type), axis=1)
df["generation"] = generate_sample_batch(df["prompt"])


if args.is_cot:
    def check_cot_match(generation, reference) -> bool:
        generation = generation.lstrip().split("Q:")[0].strip()
        reference = reference.strip()
        return reference in generation
    df["match"] = df.apply(lambda row: check_cot_match(row["generation"], row["reference"]), axis=1)
else:
    def check_match(generation, reference) -> bool:
        generation = generation.lstrip()
        reference = reference.lstrip()
        return generation.startswith(reference)
    df["match"] = df.apply(lambda row: check_match(row["generation"], row["reference"]), axis=1)

exact_match_by_task = df.groupby("task_name")["match"].mean()
exact_match = df["match"].mean() * 100

df.to_json(args.output_filepath + ".outputs.jsonl", lines=True, orient='records')

with open(args.output_filepath, "w") as f:
    f.write(json.dumps({
        "exact_match": exact_match,
        "exact_match_by_task": exact_match_by_task.to_dict()
    }))

print("Exact match: ", exact_match)
print("Exact match by task: ", exact_match_by_task)
