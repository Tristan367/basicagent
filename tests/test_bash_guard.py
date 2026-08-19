"""The destructive-command guard and the read-only classifier.

These two functions are the entire safety boundary around `bash`: there are no
permission prompts, so whatever `danger_reason` lets through simply runs. Both
directions matter — a guard that blocks ordinary work is as much a bug as one
that misses a catastrophe, because the model cannot ask a human to override it.
"""

import pytest

from agent_server.tools.bash import danger_reason, is_read_only


@pytest.mark.parametrize("command", [
    "rm -rf /",
    "rm -rf /*",
    "rm -rf ~",
    "rm -rf $HOME",
    "rm -fr /usr",
    "rm -r -f /etc",
    "/bin/rm -rf /",
    "rm --recursive --force /",
])
def test_blocks_machine_destroying_removals(command):
    assert danger_reason(command) is not None


@pytest.mark.parametrize("command", [
    "rm -rf build/",
    "rm -rf node_modules",
    "rm -rf ./dist",
    "rm -rf /home/user/projects/mysite/build",
    "rm file.txt",
    "git clean -xdf",
    "npm install",
])
def test_allows_ordinary_scoped_work(command):
    assert danger_reason(command) is None


def test_force_without_recursion_is_not_a_catastrophe():
    """`rm --force /` deletes nothing without `-r`, so it must not be blocked.

    The guard requires *both* recursion and force. Matching on either alone
    would start refusing ordinary commands, and the user has no way to override
    a refusal.
    """
    assert danger_reason("rm --force /") is None
    assert danger_reason("rm -f /tmp/x") is None
    # ...but the long spelling of both together is still the real thing.
    assert danger_reason("rm --recursive --force /") is not None


def test_flag_clusters_are_understood():
    """`-rf`, `-fr`, and separated flags are the same command."""
    assert danger_reason("rm -rf /") is not None
    assert danger_reason("rm -fr /") is not None
    assert danger_reason("rm -r -f /") is not None


def test_blocks_fork_bomb():
    assert danger_reason(":(){ :|:& };:") is not None


@pytest.mark.parametrize("command", [
    "dd if=/dev/zero of=/dev/sda",
    "mkfs.ext4 /dev/nvme0n1",
    "echo x > /dev/sda",
])
def test_blocks_raw_disk_writes(command):
    assert danger_reason(command) is not None


@pytest.mark.parametrize("command", [
    "ls -la",
    "cat README.md",
    "git status",
    "git log --oneline",
    "git diff HEAD",
    "rg TODO | head -5",
    "wc -l *.py",
])
def test_read_only_accepts_observation(command):
    assert is_read_only(command) is True


@pytest.mark.parametrize("command", [
    "rm file.txt",
    "echo hi > out.txt",
    "ls && rm -rf build",
    "sudo apt install cowsay",
    "cat $(whoami)",
    "find . -name '*.tmp' -delete",
    "find . -exec rm {} ;",
    "git branch -D main",
    "git remote add origin git@example.com:x.git",
    "npm install",
])
def test_read_only_rejects_mutation(command):
    assert is_read_only(command) is False


def test_read_only_rejects_unparseable():
    """An unbalanced quote must fail closed, not raise."""
    assert is_read_only("echo 'unterminated") is False
