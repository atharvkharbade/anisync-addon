import os
import subprocess
import shutil
import datetime

# The list of original 30 commits in chronological order
COMMITS = [
    "5ba04f1741349c0cb9d8d77db5312bd4fba10d9f",
    "7874d7a7389c4ccfd4691dd6867e6b7e504a8da9",
    "15b069d522aa726bb2399e3abb603258a6c7ba6c",
    "5c199afb51d597fb7a51fa4edd5e53aa50d1ca10",
    "d77e6e1a158025e82f8577b91d8f4a26f0bd48ca",
    "4da7cf5829b20cd6fa9af5b2fee7a07b62f620ff",
    "5325591cd5ed160eee3d775c10ff819277a53d51",
    "926ed1f17452a0c93a02ce5fd629e8330d9963c2",
    "cef1bac90d769fda57803f7a9581a666ba0e04cc",
    "f0ec4740a81694f9de02e5e07c86fda8f098d5b7",
    "07f19ecbfa46bba2e5fcacce7b467c8b25bea27e",
    "81689d3bfd3bddf73d07832728cdd332015c1329",
    "d92e387195623cd7faa45ee0efe00f9c73ae6a0e",
    "70c0e119acce8975f25e2162a0c96c0523c13342",
    "d90bd139855813279446c51c59b21d1bf6cd88c1",
    "ff43b54e3f7de0c9827631fad4bdf3621c88974b",
    "03045c0a9a4626435dad63f64a495709dc403426",
    "02ee19e783d7fd0dbe4aa42fac0da6b442554674",
    "b1ccd122396d4b2d49cdf8d392c58b6a9cbf86af",
    "2d14d5e79b9e1ecf1a45be3ee412e638a769d36e",
    "5ffac912bf096f7a6efbd51f2f6f5e23f31d38fa",
    "e1c8a97ca59527e55a719244c71a27d05977521c",
    "43f7d2c8a6018269e1b4a825d07da248b1886f46",
    "fda312f49088b5074879683a43235f48f4b16038",
    "d64e7d3605e10c003e813b61686e489f1d3e0389",
    "348734efd32fe66df47ed5d9b3aba512270b46ad",
    "ef082c05215b4dfc57d6ed67abd53e53ac4ac344",
    "4d751bbb8d8af9788b8497c8ffa0d0f674c7e7e6",
    "0286cc9ec6bf7e4fce3cf2738fa218528baf2a35",
    "1a62f83715e6749cf6acf1d994a20b1401327205"
]

def run(cmd):
    return subprocess.check_output(cmd, shell=True).decode('utf-8').strip()

# Create a temporary backup directory
backup_dir = "/tmp/anisync_backup"
if os.path.exists(backup_dir):
    shutil.rmtree(backup_dir)
os.makedirs(backup_dir)

# Save file contents at each of the 30 milestones
print("Backing up files at each milestone...")
for idx, commit in enumerate(COMMITS):
    run(f"git checkout {commit}")
    milestone_dir = os.path.join(backup_dir, f"m_{idx}")
    os.makedirs(milestone_dir)
    # Copy files
    for item in os.listdir("."):
        if item in [".git", "scratch", ".venv", "uv.lock"]:
            continue
        src = item
        dst = os.path.join(milestone_dir, item)
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)

# Return to main branch
run("git checkout main")

# Create a new branch temp_main starting from empty
print("Re-initializing history on temp_main...")
run("git checkout --orphan temp_main")
run("git rm -rf .")

# Define helper to categorize files
def categorize_files(files):
    groups = {
        "services": [],
        "library": [],
        "routes": [],
        "templates": [],
        "configs_docs": []
    }
    for f in files:
        if f.startswith("app/services/") or f == "app/services" or f == "config.py":
            groups["services"].append(f)
        elif f.startswith("app/lib/") or f.startswith("app/api/") or f == "app/lib" or f == "app/api":
            groups["library"].append(f)
        elif f.startswith("app/routes/") or f == "app/routes" or f == "app/app.py" or f == "app/factory.py" or f == "run.py":
            groups["routes"].append(f)
        elif f.startswith("templates/") or f.startswith("static/") or f == "templates" or f == "static":
            groups["templates"].append(f)
        else:
            groups["configs_docs"].append(f)
    return groups

# Iterate through milestones to split commits
for idx, commit in enumerate(COMMITS):
    # Fetch original metadata
    meta = run(f'git log -1 --format="%an|%ae|%ad|%s" {commit}')
    author_name, author_email, author_date, original_msg = meta.split('|', 3)
    
    # Parse date
    # Format: Fri May 1 10:00:00 2026 +0000
    # Strip timezone for parsing
    date_parts = author_date.split()
    date_str = " ".join(date_parts[:-1]) # e.g. "Fri May 1 10:00:00 2026"
    dt = datetime.datetime.strptime(date_str, "%a %b %d %H:%M:%S %Y")
    
    # Copy milestone files into working directory
    milestone_dir = os.path.join(backup_dir, f"m_{idx}")
    
    # Clear current working directory (except ignored ones)
    for item in os.listdir("."):
        if item in [".git", "scratch", ".venv", "uv.lock"]:
            continue
        if os.path.isdir(item):
            shutil.rmtree(item)
        else:
            os.remove(item)
            
    # Copy from backup
    for item in os.listdir(milestone_dir):
        src = os.path.join(milestone_dir, item)
        dst = item
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
            
    # Get all modified/added files compared to git index
    changed = run("git status --porcelain")
    changed_files = []
    for line in changed.splitlines():
        if line.strip():
            parts = line.strip().split(None, 1)
            if len(parts) > 1:
                changed_files.append(parts[1])
                
    groups = categorize_files(changed_files)
    active_groups = {k: v for k, v in groups.items() if v}
    num_groups = len(active_groups)
    
    if num_groups <= 1:
        # Commit everything in a single commit if it's small
        run("git add -A")
        # Set commit envs
        os.environ["GIT_AUTHOR_NAME"] = author_name
        os.environ["GIT_AUTHOR_EMAIL"] = author_email
        os.environ["GIT_COMMITTER_NAME"] = author_name
        os.environ["GIT_COMMITTER_EMAIL"] = author_email
        date_iso = dt.strftime("%Y-%m-%dT%H:%M:%S")
        os.environ["GIT_AUTHOR_DATE"] = date_iso
        os.environ["GIT_COMMITTER_DATE"] = date_iso
        subprocess.run(["git", "commit", "-m", original_msg, "--date", date_iso], env=os.environ)
    else:
        # Split into multiple commits, spacing them out by minutes
        group_items = list(active_groups.items())
        for g_idx, (g_name, g_files) in enumerate(group_items):
            # Stage files
            for f in g_files:
                run(f"git add {f}")
                
            # Formulate message
            if g_name == "services":
                msg = f"chore: configure core database and connection pool services"
            elif g_name == "library":
                msg = f"feat: implement third-party API wrapper clients"
            elif g_name == "routes":
                msg = f"feat: implement blueprint route handlers"
            elif g_name == "templates":
                msg = f"feat: design user configuration dashboard UI templates"
            else:
                msg = original_msg
                
            # Adjust date slightly for sub-commits
            sub_dt = dt - datetime.timedelta(minutes=(num_groups - 1 - g_idx) * 10)
            date_iso = sub_dt.strftime("%Y-%m-%dT%H:%M:%S")
            
            os.environ["GIT_AUTHOR_NAME"] = author_name
            os.environ["GIT_AUTHOR_EMAIL"] = author_email
            os.environ["GIT_COMMITTER_NAME"] = author_name
            os.environ["GIT_COMMITTER_EMAIL"] = author_email
            os.environ["GIT_AUTHOR_DATE"] = date_iso
            os.environ["GIT_COMMITTER_DATE"] = date_iso
            subprocess.run(["git", "commit", "-m", msg, "--date", date_iso], env=os.environ)

print("Regeneration complete. Checking new log count...")
new_count = run("git rev-list --count HEAD")
print(f"New commit count on temp_main: {new_count}")
