import os
import re

html_files = [
    "frontend/index.html",
    "frontend/watchlist.html",
    "frontend/outreach.html",
    "frontend/changes.html",
    "frontend/batch.html",
    "frontend/users.html"
]

support_nav = """
      <a href="support.html" class="nav-item" id="supportNav" style="display:none">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>
        </svg>
        <span>Support Dashboard</span>
      </a>"""

for file_path in html_files:
    if os.path.exists(file_path):
        with open(file_path, "r") as f:
            content = f.read()
            
        # Find the end of the usersNav block.
        # It usually ends with `</a>` and some spaces.
        users_nav_pattern = r'(<a href="users\.html" class="nav-item(?:[^>]+)? id="usersNav"(?:[^>]+)?>.*?</a>)'
        
        # We replace the usersNav match with itself + support_nav
        content = re.sub(users_nav_pattern, r'\1' + support_nav, content, flags=re.DOTALL)
        
        with open(file_path, "w") as f:
            f.write(content)

print("Updated sidebars")
