"""
Configuration file for Qwen3 + SAM3 integration
Modify these paths according to your environment
"""

import os
import torch
# Base directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ============================================================================
# Local weights root (override with PROC_SAM3_WEIGHTS_ROOT if needed)
# ============================================================================
WEIGHTS_ROOT = os.environ.get("PROC_SAM3_WEIGHTS_ROOT", r"E:\1代码\模型\权重")

# ============================================================================
# Model Paths
# ============================================================================

# Qwen3 Configuration - local checkpoints only
QWEN_MODELS = {
    'Qwen3_VL_4B': os.path.join(WEIGHTS_ROOT, "Qwen3-VL-4B-Instruct"),
}

# Default Qwen model
QWEN_MODEL_PATH = QWEN_MODELS['Qwen3_VL_4B']  # Path to local Qwen3 model

QWEN_DEVICE = "cuda" if torch.cuda.is_available() else "cpu"  # Device for local model

# Model type selection (for use in scripts)
QWEN_MODEL_TYPE = "qwen3"  # Can be "qwen3" or "qwen2_5"

# SAM3 Configuration
SAM3_BPE_PATH = os.path.join(WEIGHTS_ROOT, "sam3", "bpe_simple_vocab_16e6.txt.gz")
SAM3_CHECKPOINT_PATH = os.path.join(WEIGHTS_ROOT, "sam3", "sam3.pt")

# RemoteCLIP Configuration (for Step 2)
REMOTECLIP_MODELS = {
    'RN50': os.path.join(WEIGHTS_ROOT, "Remoteclip", "RemoteCLIP-RN50.pt"),
    'ViT_B_32': os.path.join(WEIGHTS_ROOT, "Remoteclip", "RemoteCLIP-ViT-B-32.pt"),
    'ViT_L_14': os.path.join(WEIGHTS_ROOT, "Remoteclip", "RemoteCLIP-ViT-L-14.pt"),
}

# Default RemoteCLIP model
REMOTECLIP_CHECKPOINT_PATH = REMOTECLIP_MODELS['ViT_L_14']
REMOTECLIP_MODEL_NAME = "ViT-L-14"

# ============================================================================
# Dataset Paths
# ============================================================================


# Alternative paths (adjust as needed)
# VAIHINGEN_IMAGE_DIR = "/path/to/your/vaihingen/images"
# VAIHINGEN_LABEL_DIR = "/path/to/your/vaihingen/labels"

# ============================================================================
# Class Files
# ============================================================================

# Original classes (one class per line)
CLS_FILE_ORIGINAL = os.path.join(BASE_DIR, "configs", "cls_vaihingen.txt")

# Qwen3 expanded prompts (comma-separated synonyms per line)
CLS_FILE_QWEN = os.path.join(BASE_DIR, "configs", "cls_vaihingen_qwen.txt")

# ============================================================================
# Test Configuration
# ============================================================================

# Number of images to test
NUM_TEST_IMAGES = 10

# Random seed for reproducibility
RANDOM_SEED = 42

# Maximum prompts per class
MAX_PROMPTS_PER_CLASS = 4

# Confidence threshold for SAM3
CONFIDENCE_THRESHOLD = 0.5

# Temperature for soft label mapping (Step 2)
TEMPERATURE = 10.0

# Alpha for text/visual similarity fusion (Step 2)
ALPHA_TEXT_VISUAL = 0.6

# Enable RemoteCLIP alignment
USE_REMOTECLIP_ALIGN = False

# ============================================================================
# Output Configuration
# ============================================================================

# Default output directory
DEFAULT_OUTPUT_DIR = os.path.join(BASE_DIR, "test_results")

# Save visualizations
SAVE_VISUALIZATIONS = True

# Save numpy arrays
SAVE_NUMPY_ARRAYS = True

# Save text summaries
SAVE_TEXT_SUMMARIES = True

# ============================================================================
# Debug Options
# ============================================================================

# Enable verbose logging
VERBOSE = True

# Save intermediate results
SAVE_INTERMEDIATE = True

# Skip evaluation (for Step 1)
SKIP_EVALUATION = True