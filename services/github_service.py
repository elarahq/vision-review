from github import Github, Auth, GithubException, Repository
from github.PullRequest import PullRequest
from github.File import File
from dotenv import load_dotenv
from utils.file_filter import FileFilter
import os
import re

class GithubService:
    def __init__(self):
        self.g = self._initialize_github()
    
    def _initialize_github(self) -> Github:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            raise ValueError("GITHUB_TOKEN environment variable is not set")
        return Github(auth=Auth.Token(token))


class GithubRepoService(GithubService):
    FILE_COUNT_THRESHOLD = 200

    def __init__(self):
        super().__init__()

    def fetch_pr_data(self, repo: str, pr_number: int) -> dict:
        repo = self.g.get_repo(repo)
        pr = repo.get_pull(pr_number)

        # First pass: collect filtered files with patches only
        filtered_files = []
        for file in pr.get_files():
            if file.status != "removed" and file.patch and FileFilter.should_review(filename=file.filename):
                filtered_files.append(file)

        patch_only = len(filtered_files) > self.FILE_COUNT_THRESHOLD
        print(f"Found {len(filtered_files)} reviewable files — mode: {'patch-only' if patch_only else 'full-context'}")

        # Second pass: build file data, skipping full content fetch for large PRs
        files = []
        for file in filtered_files:
            full_file_code = None if patch_only else self._fetch_full_file(file=file, repo=repo, pr=pr)
            files.append({
                "filename": file.filename,
                "patch": file.patch,
                "full_content": full_file_code
            })

        return {
            "title": pr.title,
            "description": pr.body or "",
            "author": pr.user.login,
            "head_sha": pr.head.sha,
            "files": files,
            "patch_only": patch_only,
        }

    def post_review(self, repo: str, pr_number: int, comments: list, commit_sha: str, pr_files: list = None):
        repo_obj = self.g.get_repo(repo)
        pr = repo_obj.get_pull(pr_number)
        commit = repo_obj.get_commit(commit_sha)

        # Build valid lines per file from diffs
        valid_lines_map = {}
        if pr_files:
            for f in pr_files:
                if f.get("patch"):
                    valid_lines_map[f["filename"]] = self._parse_valid_lines(f["patch"])

        review_comments = []
        for c in comments:
            path = c.get("path")
            line = c.get("line")
            severity = c.get("severity", "")
            body = f"**[{severity}]** {c.get('body')}" if severity else c.get("body")

            valid_lines = valid_lines_map.get(path, set())
            if valid_lines:
                if line not in valid_lines:
                    # Snap to nearest valid line
                    line = min(valid_lines, key=lambda x: abs(x - line))
                review_comments.append({"path": path, "line": line, "body": body})

        if review_comments:
            try:
                pr.create_review(
                    commit=commit,
                    event="REQUEST_CHANGES",
                    comments=review_comments
                )
                print(f"Posted {len(review_comments)} inline comments")
            except GithubException as e:
                print(f"Failed to post review: status={e.status}, message={e.data}")
                raise
        else:
            print("No valid comments to post")
        

    def approve_review(self, repo: str, pr_number: int, commit_sha: str):
        repo = self.g.get_repo(repo)
        pr = repo.get_pull(pr_number)

        pr.create_review(
            commit=repo.get_commit(commit_sha),
            event="APPROVE"
        )


    def _fetch_full_file(self, file: File, repo: Repository, pr: PullRequest) -> str | None:
        try:
            file_content = repo.get_contents(file.filename, ref=pr.head.sha)
            full_code = file_content.decoded_content.decode()
        except Exception as e:
            print(f"Could not fetch full content for {file.filename}: {e}")
            full_code = None

        return full_code

    def _parse_valid_lines(self, patch: str) -> set:
      valid_lines = set()
      current_line = None

      for line in patch.split("\n"):
          hunk_match = re.match(r'^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@', line)
          if hunk_match:
              current_line = int(hunk_match.group(1))
              continue

          if current_line is None:
              continue

          if line.startswith("+"):
              valid_lines.add(current_line)
              current_line += 1
          elif line.startswith("-"):
              pass
          else:
              valid_lines.add(current_line)
              current_line += 1

      return valid_lines




