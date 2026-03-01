import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# Scopes: 'drive.file' allows access only to files created/uploaded by this app
SCOPES = ['https://www.googleapis.com/auth/drive.file']
CLIENT_SECRETS_FILE = 'client_secrets.json'
TOKEN_FILE = 'token.json'

# Your Folder ID
FOLDER_ID = '18DMPcZcDW4ksqx0n20eOcBXlDjR6T3tN'

def get_drive_service():
    creds = None
    # The file token.json stores the user's access and refresh tokens
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())

    return build('drive', 'v3', credentials=creds)

def upload_resume_to_drive(file_stream, filename, mimetype='application/pdf'):
    service = get_drive_service()

    # Reset stream position before any read/upload
    file_stream.seek(0)

    # Check for existing file with same name in folder (avoid duplicates)
    name_escaped = filename.replace("\\", "\\\\").replace("'", "\\'")
    query = f"name='{name_escaped}' and '{FOLDER_ID}' in parents and trashed=false"
    existing = (
        service.files()
        .list(q=query, fields="files(id)", pageSize=1)
        .execute()
    )
    file_list = existing.get("files", [])

    media = MediaIoBaseUpload(file_stream, mimetype=mimetype, resumable=True)

    try:
        if file_list:
            file_id = file_list[0]["id"]
            file_stream.seek(0)
            media = MediaIoBaseUpload(file_stream, mimetype=mimetype, resumable=True)
            service.files().update(fileId=file_id, media_body=media).execute()
            print(f"File overwritten successfully. ID: {file_id}")
            return file_id
        file_metadata = {
            "name": filename,
            "parents": [FOLDER_ID],
        }
        file = (
            service.files()
            .create(body=file_metadata, media_body=media, fields="id")
            .execute()
        )
        print(f"File uploaded successfully. ID: {file.get('id')}")
        return file.get("id")
    except Exception as e:
        print(f"An error occurred uploading to Drive: {e}")
        return None