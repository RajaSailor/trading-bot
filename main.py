import requests

print("✅ Python + VS Code setup working!")
print("Status code from python.org:", requests.get("https://www.python.org").status_code)
