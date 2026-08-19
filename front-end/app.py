import os, threading, requests, schedule, time
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from bs4 import BeautifulSoup
from passlib.hash import pbkdf2_sha256
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY")  # Set the secret key for session management

url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(url, key) # Initialize Supabase client

### Job that fetches new updates from all tags daily at 12:00 AM
def tag_refresh_job():
    rss_feeds = supabase.table('Tags').select('rss_feed').execute()
    for url in rss_feeds.data:
        print(f"Fetching updates for RSS feed: {url['rss_feed']}")
        try:
            data = requests.get(url['rss_feed']).text  # Fetch the RSS feed to trigger any updates
            soup = BeautifulSoup(data, "xml")
            
            for entry in soup.find_all("entry", limit=5):
                
                work_id = entry.id.string
                title = entry.title.string
                author = entry.author.find("name").string
                source = entry.link.get("href")
                updated = entry.updated.string
                
                # Insert entry into database, update if it already exists
                supabase.table('Works').upsert({
                    'id': work_id,
                    'title': title,
                    'author_name': author,
                    'source': source,
                    'updated_at': updated,
                    'rss_feed': url['rss_feed'],
                }, on_conflict='id').execute()
                
                print(f"Updated work ID: {work_id} for RSS feed: {url['rss_feed']}")
            
            time.sleep(30)  # Sleep for 30 seconds to avoid overwhelming the server with requests
        
        except Exception as e:
            print(f"Error fetching RSS feed from {url['rss_feed']}: {e}")
            
def job_scheduler():
     # Schedule the tag refresh job to run daily at 12:00 AM
    schedule.every().day.at("00:00").do(tag_refresh_job)
    while True:
        schedule.run_pending()
        time.sleep(60)
        
scheduler_thread = threading.Thread(target=job_scheduler, daemon=True)
scheduler_thread.start()

@app.route('/')
def index():
    return render_template('index.html')

### LOGIN AND REGISTRATION ROUTES ###


@app.route('/login')
def login():
    return render_template('login.html')

# Handle login form submission
@app.route('/login-form', methods=['POST'])
def login_form():
    email = request.form.get('login-email')
    password = request.form.get('login-password')

    # Retrieve user data from DB
    user = supabase.table('Users').select('*').eq('email', email).execute()
    if user.data:
        pw_hash = pbkdf2_sha256.hash(password)
        if pbkdf2_sha256.verify(password, user.data[0]['password']):
            session['user_id'] = user.data[0]['id']  # Store user ID in session
            return render_template('dashboard.html', message="Login successful!", status=200)
        else:
            return render_template('login.html', message="Incorrect password. Please try again.", status=401)
    else:
        return render_template('login.html', message="Email not found. Please register first.", status=404)
    

# Handle registration form submission
@app.route('/register-form', methods=['POST'])
def register_form():
    email = request.form.get('register-email')

    # Check if email already exists in the database
    existing_user = supabase.table('Users').select('*').eq('email', email).execute()
    if existing_user.data:
        return render_template('login.html', message="Email already exists. Please log in.", status=400)
    else:
        password = request.form.get('register-password')
        pw_hash = pbkdf2_sha256.hash(password)
        supabase.table('Users').insert({
            'email': email,
            'password': pw_hash,
        }).execute()

        return render_template('login.html', message="Registration successful! Please log in.", status=200)

@app.route('/logout')
def logout(): 
    session.pop('user_id', None)  # Remove user ID from session
    return render_template('login.html', message="You have been logged out.", status=200)


### RSS FEED FETCHING AND PARSING FUNCTIONS ###

def fetch_and_update_tag(rss_url):
    try:
        
        time.sleep(15)  # Sleep for 15 seconds to avoid overwhelming the server with requests
        
        response = requests.get(rss_url).text
        soup = BeautifulSoup(response, 'xml')
        tag_id = soup.id.string
        tag_name = soup.title.string 
        
        print(tag_id, tag_name)

        if tag_id:
            supabase.table('Tags').update({
                'tag_id': tag_id,
                'tag_name': tag_name,
                'status': 'READY',
            }).eq('rss_feed', rss_url).execute()
            print(f"Successfully fetched and updated tag ID: {tag_id} for RSS URL: {rss_url}")
        else:
            supabase.table('Tags').update({
                'status': 'FAILED',
            }).eq('rss_feed', rss_url).execute()
            print(f"Failed to fetch tag ID for RSS URL: {rss_url}. No tag ID found in the feed.")
            
    except Exception as e:
        supabase.table('Tags').update({
            'status': 'FAILED',
        }).eq('rss_feed', rss_url).execute()
        print(f"Error fetching RSS feed from {rss_url}: {e}")


### DASHBOARD AND TAG MANAGEMENT ROUTES ###


@app.route('/dashboard')
def dashboard():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))
    
    # Get message if redirected from refresh request
    message = request.args.get('message')

    # Get RSS feeds associated with the user
    user_tags_result = supabase.table('User_Tags').select('rss_feed').eq('user_id', user_id).execute()
    rss_feeds = [item['rss_feed'] for item in user_tags_result.data or []]

    # Count favourite stories for the user
    user_works_result = supabase.table('User_Works').select('work_id').eq('user_id', user_id).execute()
    favourite_story_list = [item['work_id'] for item in user_works_result.data or []]
    favourite_story_count = len(user_works_result.data or [])

    # Count tracked tags
    tag_count = len(rss_feeds)

    # Get the newest story for each tag
    newest_stories = []
    if rss_feeds:
        works_result = supabase.table('Works').select('*').in_('rss_feed', rss_feeds).order('updated_at', desc=True).execute()
        works = works_result.data or []

        # Ensure that we only keep the newest story for each unique RSS feed
        seen_rss = set()
        for work in works:
            if work['rss_feed'] not in seen_rss:
                seen_rss.add(work['rss_feed'])
                newest_stories.append(work)

        # Get tag name for the user's RSS feeds
        tags_result = supabase.table('Tags').select('rss_feed', 'tag_name').in_('rss_feed', rss_feeds).execute()
        tag_names = {item['rss_feed']: item.get('tag_name') for item in (tags_result.data or [])}
        for story in newest_stories:
            story['tag_name'] = tag_names.get(story['rss_feed'], story.get('rss_feed'))

    new_updates_count = len(newest_stories)
    return render_template('dashboard.html', newest_stories=newest_stories, favourite_story_list=favourite_story_list, favourite_story_count=favourite_story_count, tag_count=tag_count, new_updates_count=new_updates_count, message=message, status=200)


@app.route('/tags')
def tags():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))

    else:
        user_tags_result = supabase.table('User_Tags').select('rss_feed').eq('user_id', user_id).execute()
        rss_feeds = [item['rss_feed'] for item in (user_tags_result.data or [])]

        tags = []
        if rss_feeds:
            tags_result = supabase.table('Tags').select('*').in_('rss_feed', rss_feeds).execute()
            tags = tags_result.data or []
        
        message = request.args.get('message')

        return render_template('tag.html', tags=tags, message=message)


@app.route('/add-tag', methods=['POST'])
def add_tag():
    rss_url = request.form.get('rss_url')
    print(f"Received request to add tag with RSS URL: {rss_url}")

    # Add tag into database first, then start the thread to fetch tag_id from the RSS feed

    supabase.table('Tags').upsert({
        'rss_feed': rss_url,
        'status': 'PENDING', 
    }, on_conflict='rss_feed').execute()

    supabase.table('User_Tags').upsert({
        'user_id': session.get('user_id'),
        'rss_feed': rss_url,
    }, on_conflict='user_id, rss_feed').execute()
    
    threading.Thread(
        target=fetch_and_update_tag,
        args=(rss_url,),
        daemon=True,
    ).start()

    return render_template('dashboard.html', message=f"Tag is being added.", status=202)

@app.route('/delete-tag')
def delete_tag():
    rss_feed = request.args.get('rss_feed')
    supabase.table('User_Tags').delete().eq('user_id', session.get('user_id')).eq('rss_feed', rss_feed).execute()
    
    print(f"Deleted tag with RSS feed: {rss_feed} for user ID: {session.get('user_id')}")
    
    return redirect(url_for('tags', message=f"Tag has been deleted.", status=200))

@app.route('/refresh-tags')
def refresh_tags():
        
    return redirect(url_for('dashboard', message="Fetching updates...", status=202))


@app.route('/favourites')
def favourites():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))

    user_works_result = supabase.table('User_Works').select('work_id').eq('user_id', user_id).execute()
    work_ids = [item['work_id'] for item in (user_works_result.data or [])]

    favourites = []
    if work_ids:
        works_result = supabase.table('Works').select('*').in_('id', work_ids).execute()
        favourites = works_result.data or []
        
    message = request.args.get('message')

    return render_template('favourite.html', favourites=favourites, message=message)

@app.route('/add-fav')
def add_fav():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))

    work_id = request.args.get('work_id')
    
    supabase.table('User_Works').upsert({
        'user_id': user_id,
        'work_id': work_id,
    }, on_conflict='user_id, work_id').execute()

    return redirect(url_for('dashboard', message='Added as favourite.'))

@app.route('/delete-fav')
def delete_fav():
    work_id = request.args.get('work_id')
    supabase.table('User_Works').delete().eq('user_id', session.get('user_id')).eq('work_id', work_id).execute()
    
    print(f"Deleted story {work_id}")
    
    return redirect(url_for('favourites', message=f"Story has been deleted.", status=200))
    
    

### ADMIN AND USER ROUTES ###


@app.route('/user')
def user():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))

    user_result = supabase.table("Users").select('*').eq('id', user_id).execute()
    user_email = user_result.data[0]['email'] if user_result.data else ''
    user_pw_hash = user_result.data[0]['password'] if user_result.data else ''

    message = request.args.get('message')

    return render_template('user.html', user_email=user_email, user_pw=user_pw_hash, message=message)

@app.route('/update-user', methods=["POST"])
def update_user():
    user_id = session.get('user_id')
    email = request.form.get('email')

    supabase.table('Users').update({
            'email': email,
        }).eq('id', user_id).execute()

    return redirect(url_for('user', message="Email changed."))

@app.route('/update-pw', methods=["POST"])
def update_pw():
    user_id = session.get('user_id')
    current_pw = request.form.get('current_pw')
    new_pw = request.form.get('new_pw')

@app.route('/admin')
def admin():
    return render_template('admin.html')

if __name__ == '__main__':
    app.run(debug=True, port=5000)
