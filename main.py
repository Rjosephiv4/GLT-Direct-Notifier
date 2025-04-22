import smtplib
import datetime
import os
from dotenv import load_dotenv
from supabase import create_client


#Loading in environment variables 
load_dotenv()

sender = os.getenv("SENDER")
receivers = os.getenv("CONTACT_INFORMATION")
receivers = receivers.split(",")
password = os.getenv("EMAIL_PASSWORD")
supabase_url = os.getenv("DATA_BASE_URL")
supabase_key = os.getenv("DATA_BASE_KEY")


# Create a Supabase client
supabase = create_client(supabase_url, supabase_key)



def get_api_data():
    """
    Fetches data from the API and returns the values as a dictionary so they can be 
    compared to previous's data."""
    #temporary mock data until we can get the API access. 
    product = "product"
    isAbsolute = "isAbsolute"
    api_data =[
        {
            product: "Test",
            isAbsolute: True
        }
        ,
        {
            product: "Test2",
            isAbsolute: True
        }
        ,
        {
            product: "Test3",
            isAbsolute: False
        }
    ]

    return api_data


def get_previous_data():
    """
    Fetches data from the supabase database and return the values as a dictionary so they can be compared to the current data. 
    """
    priorReadings = supabase.table("PriorReadings")
    data = supabase.table("PriorReadings").select("*").execute()
    data = data.data
    if data == []:
        print("No previous data found.")
        return None
    else:
        # Assuming the first entry is the most recent
        previous_data = data
        return previous_data

def check_changes(api_data, previous_data):
    """
    Compares the data from the API to yesterday and returns a list of changes so we can format the string email
    """
    changes = []
    previous_data = get_previous_data()
    api_data = get_api_data()
    for i in range(len(api_data)):
        if api_data[i]['product'] == previous_data[i]['product']:
            if api_data[i]['isAbsolute'] != previous_data[i]['isAbsolute']:
                changes.append({
                    'product': api_data[i]['product'],
                    'isAbsolute': api_data[i]['isAbsolute']
                })
        else:
            print("Product names do not match.")
            return []


    return changes #should be a list of the changes that occurred

def format_email(changes):
    """
    Formats the email to be sent in a tabular format.
    """
    if not changes:
        return "Nothing to report, no changes were made to the pricing fields.\n\n"
    
    # Header for the table
    email_body = "The following changes were made to the pricing fields:\n\n"
    email_body += f"{'Product':<20}{'Old Pricing':<15}{'New Pricing':<15}\n"
    email_body += "-" * 50 + "\n"

    # Add rows for each change
    for change in changes:
        old_pricing = "Relative" if change['isAbsolute'] else "Absolute"
        new_pricing = "Absolute" if change['isAbsolute'] else "Relative"
        email_body += f"{change['product']:<20}{old_pricing:<15}{new_pricing:<15}\n"

    return email_body





def send_email(sender, receivers, password, body):
    """Send an email using SMTP with the given sender, receivers, password, and body."""

    message =f"""From: {sender}
To: {receivers}
Subject: Changes as of {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}

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



change = check_changes(get_api_data(), get_previous_data())
send_email(sender, receivers, password, format_email(change))
