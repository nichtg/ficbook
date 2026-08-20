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

def current_user_is_admin():
    user_id = session.get('user_id')
    if not user_id:
        return False

    user_result = supabase.table('Users').select('admin').eq('id', user_id).execute()
    return bool(user_result.data and user_result.data[0].get('admin'))

@app.context_processor
def inject_admin_status():
    return {'is_admin': current_user_is_admin()}

# Job that fetches new updates from all tags daily at 12:00 AM
def tag_refresh_job(rss_feeds=None):
    if rss_feeds is None:
        tag_result = supabase.table('Tags').select('rss_feed').execute()
        rss_feeds = [item['rss_feed'] for item in (tag_result.data or [])]

    for rss_feed in rss_feeds:
        print(f"Fetching updates for RSS feed: {rss_feed}")
        try:
            data = requests.get(rss_feed).text  # Fetch the RSS feed to trigger any updates
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
                    'rss_feed': rss_feed,
                }, on_conflict='id').execute()
                
                print(f"Updated work ID: {work_id} for RSS feed: {rss_feed}")
            
            time.sleep(30)  # Sleep for 30 seconds to avoid overwhelming the server with requests
        
        except Exception as e:
            print(f"Error fetching RSS feed from {rss_feed}: {e}")
            
# def job_scheduler():
#      # Schedule the tag refresh job to run daily at 12:00 AM
#     schedule.every().day.at("00:00").do(tag_refresh_job)
#     while True:
#         schedule.run_pending()
#         time.sleep(60)

@app.route('/')
def index():
    return render_template('index.html')

### LOGIN AND REGISTRATION ROUTES ###


@app.route('/login')
def login():
    message = request.args.get('message')
    status = request.args.get('status', default=200, type=int)
    
    return render_template('login.html', message=message, status=status)

# Handle login form submission
@app.route('/login-form', methods=['POST'])
def login_form():
    email = request.form.get('login-email')
    password = request.form.get('login-password')

    # Retrieve user data from DB
    user = supabase.table('Users').select('*').eq('email', email).execute()
    if user.data:
        if pbkdf2_sha256.verify(password, user.data[0]['password']):
            session['user_id'] = user.data[0]['id']  # Store user ID in session
            return redirect(url_for('dashboard', message="Login successful!", status=200))
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
        works_result = (supabase.table('Works').select('*')
                .in_('rss_feed', rss_feeds)
                .order('updated_at', desc=True)
                .order('id', desc=True)
                .execute())
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

    return redirect(url_for('dashboard', message="Tag is being added.", status=202))

@app.route('/delete-tag')
def delete_tag():
    rss_feed = request.args.get('rss_feed')
    supabase.table('User_Tags').delete().eq('user_id', session.get('user_id')).eq('rss_feed', rss_feed).execute()
    
    print(f"Deleted tag with RSS feed: {rss_feed} for user ID: {session.get('user_id')}")
    
    return redirect(url_for('tags', message=f"Tag has been deleted.", status=200))

@app.route('/refresh-tags')
def refresh_tags():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))

    user_tags_result = supabase.table('User_Tags').select('rss_feed').eq('user_id', user_id).execute()
    rss_feeds = [item['rss_feed'] for item in (user_tags_result.data or [])]
    tag_refresh_job(rss_feeds)

    return redirect(url_for('dashboard', message="Fetching updates...", status=200))


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
    
    print(f"Deleted story {work_id} from favourites.")
    
    return redirect(url_for('favourites', message="Story has been removed from favourites.", status=200))
    
    

### ADMIN AND USER ROUTES ###


@app.route('/user')
def user():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))

    user_result = supabase.table("Users").select('*').eq('id', user_id).execute()
    user_email = user_result.data[0]['email'] if user_result.data else ''

    message = request.args.get('message')
    status = request.args.get('status', default=200, type=int)

    return render_template('user.html', user_email=user_email, message=message, status=status)

@app.route('/update-user', methods=["POST"])
def update_user():
    user_id = session.get('user_id')
    email = request.form.get('email')

    supabase.table('Users').update({
            'email': email,
        }).eq('id', user_id).execute()

    return redirect(url_for('user', message="Email changed.", status=200))

@app.route('/update-pw', methods=["POST"])
def update_pw():
    user_id = session.get('user_id')
    current_pw = request.form.get('current_pw')
    new_pw = request.form.get('new_pw') 
    
    user_result = supabase.table('Users').select('password').eq('id', user_id).execute()
    
    if pbkdf2_sha256.verify(current_pw, user_result.data[0]['password']):
        supabase.table('Users').update({
            'password': pbkdf2_sha256.hash(new_pw)
        }).eq('id', user_id).execute()
        return redirect(url_for('user', message="Password changed.", status=200))
    else:
        return redirect(url_for('user', message="Current password is incorrect. Please try again.", status=400))
    
@app.route('/delete-user')
def delete_user():
    user_id = session.get('user_id')
    
    supabase.table('User_Tags').delete().eq('user_id', user_id).execute()
    print(f"Deleted all tags for user {user_id}.")
    
    supabase.table('User_Works').delete().eq('user_id', user_id).execute()
    print(f"Deleted all works for user {user_id}.")
    
    supabase.table('Users').delete().eq('id', user_id).execute()
    print(f"Deleted user {user_id}. Redirecting to login page")
    
    session.pop('user_id', None) 
        
    return redirect(url_for('login', message="Account deleted.", status=200))
    

@app.route('/admin')
def admin():
    user_id = session.get('user_id')
    if not user_id:
        return render_template('403.html', logged_in=False), 403

    if current_user_is_admin():
        users = supabase.table('Users').select('id, email, admin').order('id').execute()
        message = request.args.get('message')
        status = request.args.get('status', default=200, type=int)
        
        return render_template('admin.html', users=users.data or [], message=message, status=status,)

    return render_template('403.html', logged_in=True), 403

@app.route('/admin/update-user', methods=['POST'])
def admin_update_user():
    if not current_user_is_admin():
        return render_template('403.html', logged_in=bool(session.get('user_id'))), 403

    user_id = request.form.get('user_id')
    email = request.form.get('email', '').strip()
    is_admin = request.form.get('admin') == 'on'
    if not user_id or not email:
        return redirect(url_for('admin', message='Email is required.', status=400))

    supabase.table('Users').update({'email': email, 'admin': is_admin}).eq('id', user_id).execute()
    return redirect(url_for('admin', message='User updated.', status=200))

@app.route('/admin/delete-user', methods=['POST'])
def admin_delete_user():
    if not current_user_is_admin():
        return render_template('403.html', logged_in=bool(session.get('user_id'))), 403

    user_id = request.form.get('user_id')
    if not user_id or str(user_id) == str(session.get('user_id')):
        return redirect(url_for('admin', message='You cannot delete your own account here.', status=400))

    supabase.table('User_Tags').delete().eq('user_id', user_id).execute()
    supabase.table('User_Works').delete().eq('user_id', user_id).execute()
    supabase.table('Users').delete().eq('id', user_id).execute()
    
    return redirect(url_for('admin', message='User deleted.', status=200))

if __name__ == '__main__':
    # scheduler_thread = threading.Thread(target=job_scheduler, daemon=True)
    # scheduler_thread.start()
    app.run(debug=True, port=1234)
