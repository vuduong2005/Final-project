# Starts Ollama in CPU-only mode.
# This only affects THIS process/window - it does not set any permanent
# or global environment variable, so RVC and everything else keeps using
# the GPU normally in their own terminals.
 
$env:CUDA_VISIBLE_DEVICES = "-1"
ollama serve