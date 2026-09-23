@echo off
rem Train the kotoha LoRA (kohya_ss / sd-scripts, SDXL, tuned for RTX 3070 8GB).
rem Run python -m tools.sprite_gen.lora.prepare first to build dataset\.
rem Output goes to Stability Matrix Models\Lora\kotoha\ (visible to ComfyUI as kotoha/...).
rem If it runs out of VRAM, re-run with --fp8_base appended.
setlocal
set KOHYA=C:\StabilityMatrix\Data\Packages\kohya_ss
set BASE=C:\StabilityMatrix\Data\Models\StableDiffusion\animagineXL40_v4Opt.safetensors
set OUT=C:\StabilityMatrix\Data\Models\Lora\kotoha
set HERE=%~dp0
set PYTHONIOENCODING=utf-8

cd /d %KOHYA%\sd-scripts
%KOHYA%\venv\Scripts\accelerate.exe launch --num_cpu_threads_per_process 2 sdxl_train_network.py ^
  --pretrained_model_name_or_path "%BASE%" ^
  --train_data_dir "%HERE%dataset" ^
  --output_dir "%OUT%" --output_name kotoha --save_model_as safetensors ^
  --logging_dir "%HERE%logs" ^
  --resolution 768,768 --enable_bucket --min_bucket_reso 512 --max_bucket_reso 1024 ^
  --network_module networks.lora --network_dim 16 --network_alpha 8 --network_train_unet_only ^
  --learning_rate 1e-4 --lr_scheduler cosine --lr_warmup_steps 50 --optimizer_type AdamW8bit ^
  --train_batch_size 1 --max_train_epochs 6 --save_every_n_epochs 1 ^
  --mixed_precision bf16 --save_precision bf16 --no_half_vae ^
  --gradient_checkpointing --sdpa ^
  --cache_latents --cache_latents_to_disk --cache_text_encoder_outputs --cache_text_encoder_outputs_to_disk ^
  --caption_extension .txt ^
  --seed 20260923 ^
  %*
endlocal
