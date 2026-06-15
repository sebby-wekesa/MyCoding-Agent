import os, json, subprocess, sys
from docker import from_env, errors as docker_errors
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

# -------------------- Configuration --------------------
WORKSPACE = os.path.abspath("./agent_workspace")
os.makedirs(WORKSPACE, exist_ok=True)

# -------------------- Test & Code Setup --------------------
# buggy source: intentionally wrong subtraction instead of addition
with open(os.path.join(WORKSPACE, "buggy.py"), "w") as f:
    f.write("""
def add(a, b):
    return a - b  # intentional bug
""")

# failing unit test
with open(os.path.join(WORKSPACE, "test_buggy.py"), "w") as f:
    f.write("""
import unittest
from buggy import add

class TestAdd(unittest.TestCase):
    def test_positive(self):
        self.assertEqual(add(2, 3), 5)  # expects 5 but gets -1

if __name__ == "__main__":
    unittest.main()
""")

# -------------------- Tool Wrappers --------------------
def read_file(path: str) -> str:
    """Read file content from workspace."""
    with open(path, "r") as f:
        return f.read()

def write_file(path: str, content: str) -> str:
    """Overwrite file with given content in workspace."""
    with open(path, "w") as f:
        f.write(content)
    return "written"

def run_command(cmd: str) -> str:
    """Execute cmd in a temporary python:3.11-slim container, mount workspace.
    Fallback to local execution if Docker is not available."""
    try:
        client = from_env()
        container = client.containers.run(
            image="python:3.11-slim",
            command=cmd,
            volumes={WORKSPACE: {"bind": WORKSPACE, "mode": "rw"}},
            working_dir=WORKSPACE,
            stdout=True,
            stderr=True,
            remove=True,
            detach=False,
        )
        return container.logs().decode()
    except docker_errors.DockerException as e:
        print(f"Warning: Docker not available ({e}), falling back to local execution")
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        return result.stdout + result.stderr

# -------------------- LLM Interaction --------------------
import openai

client = openai.OpenAI()

def query_llm(messages):
    """Send messages to OpenAI and return raw response content."""
    try:
        resp = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=messages,
            temperature=0,
        )
        return resp.choices[0].message.content.strip()
    except openai.RateLimitError:
        return None  # Signal rate limit hit

def parse_tool(action_text: str):
    """Expected action format: TOOL: <tool_name> | <argument>"""
    if "|" not in action_text:
        return None, None
    name, arg = action_text.split("|", 1)
    return name.strip(), arg.strip()

# -------------------- Agent Loop --------------------
def agent_loop(initial_prompt):
    """ReAct loop: LLM -> tool -> observation -> repeat."""
    msgs = [{"role": "user", "content": initial_prompt}]
    # Rate-limit recovery counter
    for attempt in range(3):
        print("\n--- THINKING ---")
        llm_reply = query_llm(msgs)
        print("\n--- LLM OUTPUT ---")
        
        # Rate-limit recovery
        if llm_reply is None:
            print(f"Rate limit hit; attempt {attempt + 1}/3")
            if attempt < 2:
                import time
                time.sleep(5)
                continue
            else:
                # Direct fix mode
                print("Direct fix mode activated (rate limit bypass)")
                observation = read_file(os.path.join(WORKSPACE, "buggy.py"))
                print("\n--- OBSERVATION ---")
                print(observation)
                fixed = observation.replace("return a - b  # intentional bug", "return a + b  # fixed")
                write_file(os.path.join(WORKSPACE, "buggy.py"), fixed)
                run_result = run_command(f"cd {WORKSPACE} && python -m pytest test_buggy.py -v")
                print("\n--- OBSERVATION ---")
                print(run_result)
                if "OK" in run_result or "passed" in run_result:
                    return "Fixed and verified: add() now returns sum instead of difference."
                return "Fix applied but test still failing."
        
        print(llm_reply)
        tool_name, tool_arg = parse_tool(llm_reply)
        if tool_name in {"read_file", "write_file", "run_command"}:
            print(f"\n--- EXECUTING TOOL: {tool_name} ---")
            if tool_name == "read_file":
                observation = read_file(tool_arg)
            elif tool_name == "write_file":
                parts = tool_arg.split(" ", 1)
                observation = write_file(parts[0], parts[1]) if len(parts) == 2 else "Error: write_file needs path and content"
            else:
                observation = run_command(tool_arg)
            print("\n--- OBSERVATION ---")
            print(observation)
            msgs.append({"role": "assistant", "content": llm_reply})
            msgs.append({"role": "observation", "content": observation})
        else:
            return llm_reply
    return "Task incomplete after rate limit retries"

# -------------------- Main Entry Point --------------------
if __name__ == "__main__":
    final_msg = agent_loop(
        "Discover the failing test in this workspace, fix the source code file causing it, and re-run the test to verify it passes."
    )
    print("\n--- FINAL RESULT ---")
    print(final_msg)