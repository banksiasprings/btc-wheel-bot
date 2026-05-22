"""Quick smoke-test: verify policy.forward() works without numpy bridge."""
import sys
sys.path.insert(0, '/Users/openclaw/Documents/btc-wheel-bot')

import torch as th
import numpy as np

# Simulate what _build_obs returns
obs = np.zeros(12, dtype=np.float32)

# Test bridge-free approach
obs_tensor = th.FloatTensor(obs.tolist()).unsqueeze(0)
print(f"torch.FloatTensor from list: OK shape={obs_tensor.shape} dtype={obs_tensor.dtype}")

# Load model and test forward pass
from stable_baselines3 import PPO
model = PPO.load('/Users/openclaw/Documents/btc-wheel-bot/rl_agent/checkpoints/best_model.zip')
model.policy.set_training_mode(False)
with th.no_grad():
    actions, _, _ = model.policy.forward(obs_tensor, deterministic=True)
action = int(actions.squeeze().item())
print(f"PPO policy.forward() OK, action={action} (0=HOLD 1=SELL_PUT_020 2=SELL_PUT_025 3=SELL_CALL_020 4=CLOSE)")
print("PASS")
