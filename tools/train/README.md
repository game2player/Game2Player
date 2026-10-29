# Train from SFT data

Train on the `train.jsonl` files and images from [data export](../datagen/README.md).
Unpack a downloaded data package first, as its README describes.

## Install

```bash
conda env create -f tools/train/environment.yaml
conda activate game2player-swift
python -m pip install flash-attn==2.8.3 --no-build-isolation
```

The environment uses PyTorch for CUDA 12.8. Change the PyTorch wheels in
`environment.yaml` if your GPUs need another build.

## Run training

```bash
python -m tools.train data/datasets/my_dataset/policy data/datasets/my_dataset/vlm \
  --output output/game2player-4b --gpus 0,1,2,3,4,5,6,7
```

Pass dataset folders or `train.jsonl` files. Images are found relative to each file.
`--dry-run` prints the training command without running it.

## Recipes

The default, `tools/train/qwen35_full.yaml`, is the recipe of Game2Player-4B: full
fine-tuning of Qwen3.5-4B with the vision encoder frozen, for one epoch at learning
rate 1e-5, with samples packed into sequences of 25,000 tokens and DeepSpeed ZeRO-2.
It ran on 8 GPUs of 80 GB, with 2 sequences per GPU and 3 accumulation steps. On
fewer GPUs, raise `gradient_accumulation_steps` to keep 48 sequences per step.

`tools/train/qwen35_lora.yaml` trains a LoRA adapter of rank 64 instead and needs far
less memory. Select a recipe with `--config`. Options after `--` go to ms-swift,
for example another base model:

```bash
python -m tools.train data/datasets/my_dataset/policy \
  --output output/qwen35-9b --gpus 0,1,2,3,4,5,6,7 -- --model Qwen/Qwen3.5-9B
```
