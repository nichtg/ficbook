from bs4 import BeautifulSoup
import requests

url = "https://archiveofourown.org/tags/80790036/feed.atom"

data = requests.get(url).text
soup = BeautifulSoup(data, "xml")

tag_id = soup.id.string
tag_name = soup.title.string 
if tag_id:
    print(f"Tag ID: {tag_id}")
    print(f"Tag Name: {tag_name}")


for entry in soup.find_all("entry", limit=5):

    if entry:
        id = entry.id.string
        title = entry.title.string
        author = entry.author.find("name").string
        source = entry.link.get("href")
        updated = entry.updated.string

    print(f"ID: {id}")
    print(f"Title: {title}")
    print(f"Author: {author}")
    print(f"Updated: {updated}")
    print(f"Source: {source}")
    print("-" * 40)