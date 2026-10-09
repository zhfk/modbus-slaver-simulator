"""Keep the failure tail visible in Actions annotations for native builds."""

import os
import subprocess
import sys


if __name__ == "__main__":
    result = subprocess.run(
        sys.argv[1:],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
    )
    print(result.stdout, end="")
    if result.returncode:
        tail = result.stdout[-16000:]
        annotation = tail.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print("::error title=Native release check failed::" + annotation)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(
                os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8"
            ) as summary:
                summary.write(
                    "### Native check failure\n\n```text\n" + tail + "\n```\n"
                )
    sys.exit(result.returncode)
