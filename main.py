import smtplib
import datetime
import os
from dotenv import load_dotenv

#Loading in environment variables 
load_dotenv()

sender = os.getenv("SENDER")
receivers = os.getenv("CONTACT_INFORMATION")
receivers = receivers.split(",")

password = os.getenv("EMAIL_PASSWORD")

def get_api_data():
    """
    Fetches data from the API and returns the values as a dictionary so they can be 
    compared to previous's data."""

def get_previous_data():
    """
    """
def check_changes():
    """
    Compares the data from the API to yesterday and returns a list of changes so we can format the string email
    """

def format_email():
    """
    Formats the email to be sent. 
    """


def send_email(sender, receivers, password, body):
    """Send an email using SMTP with the given sender, receivers, password, and body."""
    body = body
    sender = sender
    receivers = receivers
    password = password


    message = f"""From: {sender}
        To: {receivers}
        Subject: Test Email {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}
        {body}
    """


    try: 
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
    except Exception as e:
        print(f"Error starting server: {e}")
        exit()


    try:
        server.login(sender, password)
        print("Login successful")
    except smtplib.SMTPAuthenticationError:
        print("Error: Unable to login. Check your email and password.")
        exit()

    try:
        server.sendmail(sender, receivers, message)
        print("Email sent successfully")
    except Exception as e:
        print(f"Error sending email: {e}")
