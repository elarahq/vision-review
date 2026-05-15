from langchain_anthropic import ChatAnthropic
from prompts.code_review import REVIEW_SYSTEM_PROMPT, REVIEW_USER_PROMPT, PATCH_ONLY_ADDENDUM
from pydantic import BaseModel, field_validator
from typing import Literal
import json
import os
import re


class ReviewComment(BaseModel):
    path: str
    line: int
    severity: Literal["HIGH", "MEDIUM", "LOW"]
    confidence: int  # 0-100: how confident Claude is that this comment is correct and not assumption-based
    body: str


class ReviewResponse(BaseModel):
    comments: list[ReviewComment]

    @field_validator("comments", mode="before")
    @classmethod
    def parse_comments_string(cls, v):
        if isinstance(v, str):
            return json.loads(v)
        return v


GUIDELINES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts", "guidelines")

REPO_GUIDELINES = {
    "elarahq/housing.brahmand": ["housing.brahmand.md"],
    "elarahq/housing.seller": ["housing.seller.md"],
    "elarahq/housing.seo": ["housing.seo.md"],
    "elarahq/khoj": ["khoj.md"],
    "elarahq/housing-app": [],
}

     
PLATFORM_GUIDELINES = {
    ".swift": "iOS.md",
    ".m": "iOS.md",
    ".h": "iOS.md",
    ".xib": "iOS.md",
    ".storyboard": "iOS.md",
    ".kt": "Android.md",
    ".java": "Android.md",
}

sonnet_4_6 = "claude-sonnet-4-6"
sonnet_4_5 = "claude-sonnet-4-5-20250929"

class ClaudeService:
    def __init__(self):
        self.llm = ChatAnthropic(model=sonnet_4_6, temperature=0, max_tokens=16384)
        self.structured_llm = self.llm.with_structured_output(ReviewResponse)

    def review_pr(self, pr_data: dict, repo: str, github_service) -> list:
        patch_only = pr_data.get("patch_only", False)
        guidelines = self._load_guidelines(repo=repo, files=pr_data.get("files"), head_sha=pr_data.get("head_sha"), github_service=github_service)
        system_text = REVIEW_SYSTEM_PROMPT
        if patch_only:
            system_text += "\n" + PATCH_ONLY_ADDENDUM
        system_text += "\n\nGUIDELINES:\n" + guidelines

        batches = self._create_batches(files=pr_data.get("files"))
        print(f"Split into {len(batches)} batches")

        all_comments = []
        for i, batch in enumerate(batches):
            print(f"Reviewing batch {i+1}/{len(batches)} ({len(batch)} files)")
            comments = self._review_batch(batch=batch, system_text=system_text, patch_only=patch_only)
            all_comments.extend(comments)

        return all_comments

    def _fetch_vision_md(self, repo: str, head_sha: str, github_service) -> str | None:
        try:
            repo_obj = github_service.g.get_repo(repo)
            content = repo_obj.get_contents("vision.md", ref=head_sha)
            return content.decoded_content.decode()
        except Exception:
            return None

    def _load_guidelines(self, repo: str, files: list, head_sha: str, github_service) -> str:
        guidelines = ""
        loaded = []

        try:
            common_path = os.path.join(GUIDELINES_DIR, "Common.md")
            with open(common_path) as f:
                guidelines = f.read()
            loaded.append("Common.md")
        except FileNotFoundError:
            print(f"Warning: Common.md not found at {common_path}")

        vision_md = self._fetch_vision_md(repo, head_sha, github_service)
        if vision_md:
            guidelines += "\n\n" + vision_md
            loaded.append("vision.md (from repo)")
        else:
            repo_guidelines = REPO_GUIDELINES.get(repo, [])
            for filename in repo_guidelines:
                try:
                    filepath = os.path.join(GUIDELINES_DIR, filename)
                    with open(filepath) as f:
                        guidelines += "\n\n" + f.read()
                    loaded.append(filename)
                except FileNotFoundError:
                    print(f"Warning: {filename} not found")

        if repo == "elarahq/housing-app" and files:
            platform_file = self._detect_platform(files)
            if platform_file:
                try:
                    filepath = os.path.join(GUIDELINES_DIR, platform_file)
                    with open(filepath) as f:
                        guidelines += "\n\n" + f.read()
                    loaded.append(platform_file)
                except FileNotFoundError:
                    print(f"Warning: {platform_file} not found")

        print(f"Loaded guidelines: {' + '.join(loaded) if loaded else 'none'}")
        return guidelines

    def _detect_platform(self, files: list) -> str | None:
        for file in files:
            ext = os.path.splitext(file.get("filename", ""))[1].lower()
            if ext in PLATFORM_GUIDELINES:
                return PLATFORM_GUIDELINES[ext]
        return None

    def _create_batches(self, files: list, max_tokens=150_000):
        batches = []
        current_batch = []
        current_tokens = 0

        for file in files:
            tokens = (len(file.get("full_content") or "") + len(file.get("patch") or "")) // 4

            if current_tokens + tokens > max_tokens and current_batch:
                batches.append(current_batch)
                current_batch = [file]
                current_tokens = tokens
            else:
                current_batch.append(file)
                current_tokens += tokens
        
        if current_batch:
            batches.append(current_batch)

        return batches

    def _review_batch(self, batch: list, system_text: str, patch_only: bool = False) -> list:

        all_files_text = self._format_files(files=batch, patch_only=patch_only)

        messages = [
              {
                  "role": "system",
                  "content": [
                      {
                          "type": "text",
                          "text": system_text,
                          "cache_control": {"type": "ephemeral"}
                      }
                  ],
              },
              {
                  "role": "user",
                  "content": REVIEW_USER_PROMPT.format(all_files=all_files_text),
              },
          ]

        try:
            response = self.structured_llm.invoke(messages)
            return [c.model_dump() for c in response.comments]
        except Exception as e:
            print(f"Failed to get structured review response: {e}")
            raise


    def _format_files(self, files: list, patch_only: bool = False) -> str:
        formatted_files = []

        for file in files:
            annotated_patch = self._annotate_patch(file.get("patch") or "")

            file_text = f"""
                {'='*60}
                FILE: {file.get("filename")}
                {'='*60}

                CHANGES (DIFF) — each line prefixed with its line number. ":" = context, "+" = added, "-" = deleted.
                {annotated_patch}"""

            if not patch_only:
                full_content = file.get("full_content")
                annotated_full = self._annotate_full_file(full_content) if full_content else "[Content not available]"
                file_text += f"""

                {'~'*60}
                FULL FILE CONTEXT — each line prefixed with its line number.
                {'~'*60}
                {annotated_full}"""

            formatted_files.append(file_text)

        return "\n\n".join(formatted_files)

    def _annotate_full_file(self, content: str) -> str:
        """Prefix every line with its 1-indexed line number, e.g. '15: def foo():'."""
        if not content:
            return content
        lines = content.split("\n")
        return "\n".join(f"{i + 1}: {line}" for i, line in enumerate(lines))

    def _annotate_patch(self, patch: str) -> str:
        """
        Annotate each line of a unified diff with its line number, using dual counters
        for the old and new files. Hunk headers are kept as-is.

        Output format per line:
          "15: def foo():"          -- context line at new-file line 15
          "16+ if x is None:"       -- added line at new-file line 16
          "11- if x < 0:"           -- deleted line at old-file line 11
        """
        if not patch:
            return patch

        annotated = []
        old_line = None
        new_line = None

        for line in patch.split("\n"):
            hunk_match = re.match(r'^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@', line)
            if hunk_match:
                old_line = int(hunk_match.group(1))
                new_line = int(hunk_match.group(2))
                annotated.append(line)
                continue

            # Lines before the first hunk header (file headers etc.) pass through unchanged.
            if old_line is None or new_line is None:
                annotated.append(line)
                continue

            # "\ No newline at end of file" marker — pass through, do not increment.
            if line.startswith("\\"):
                annotated.append(line)
                continue

            if line.startswith("+"):
                annotated.append(f"{new_line}+ {line[1:]}")
                new_line += 1
            elif line.startswith("-"):
                annotated.append(f"{old_line}- {line[1:]}")
                old_line += 1
            else:
                content = line[1:] if line.startswith(" ") else line
                annotated.append(f"{new_line}: {content}")
                old_line += 1
                new_line += 1

        return "\n".join(annotated)

