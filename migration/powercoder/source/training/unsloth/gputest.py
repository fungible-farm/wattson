import torch, sys
print("torch", torch.__version__)
print("cuda available:", torch.cuda.is_available())
if not torch.cuda.is_available():
    print("CUDA INIT ERROR:", torch.cuda.init()); sys.exit(1)
print("device count:", torch.cuda.device_count())
print("device name:", torch.cuda.get_device_name(0))
print("capability:", torch.cuda.get_device_capability(0))
print("total mem (GiB):", round(torch.cuda.get_device_properties(0).total_memory/1e9,1))
a = torch.randn(2048, 2048, device="cuda", dtype=torch.bfloat16)
b = torch.randn(2048, 2048, device="cuda", dtype=torch.bfloat16)
for _ in range(50):
    c = a @ b
torch.cuda.synchronize()
print("GPU matmul OK, result sum:", float(c.sum()))
print("GPU TEST PASS")
