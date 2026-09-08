# USAJOBS Job-Search Agent

A Streamlit review desk for federal jobs in these search families:

- Program Analyst
- IT
- Data Analyst
- Artificial Intelligence

It filters by minimum GS level, location, and remote preference, then ranks results against the skills and experience you enter. Results include title, agency, grade, location, salary, closing date, fit score, and match reasons.

## Run

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Open the local URL shown by Streamlit.

## Deployment

The app is configured for Render with `render.yaml` and `Procfile`.

1. Push this project to a GitHub repository.
2. In Render, choose **New +** and **Blueprint**.
3. Connect the GitHub repository and deploy the existing `render.yaml`.
4. Add `USAJOBS_API_KEY` and `USAJOBS_EMAIL` as Render environment variables.

The local development URL is `http://localhost:8503/`. A public URL is created by Render after deployment.

To connect this local repository to GitHub, replace the URL below with the repository URL:

```powershell
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git
git branch -M main
git add .
git commit -m "Prepare USAJOBS review desk for deployment"
git push -u origin main
```

## Live USAJOBS data

The app uses demo data until both values are supplied in the sidebar:

- USAJOBS API key
- USAJOBS account email

Request an API key through the USAJOBS developer resources. For Streamlit deployment, configure these secrets in the deployment settings using `.streamlit/secrets.toml.example` as a template:

```toml
USAJOBS_API_KEY = "your-api-key"
USAJOBS_EMAIL = "your-account-email"
```

For local development, the app also accepts `USAJOBS_API_KEY` and `USAJOBS_EMAIL` environment variables. The credentials are server-configured and are not shown in the UI. Live search results are cached for five minutes to reduce API usage.

Do not commit `.streamlit/secrets.toml` or `.env` files.

## Matching scope

The app preserves structured USAJOBS fields when supplied by the API, including duties, qualifications, specialized experience, education, certifications, and required documents. Profile categories are compared against the relevant announcement fields. Matching is a screening aid and does not determine eligibility or replace review of the official announcement.

## Application safety

This agent does not log into USAJOBS, complete forms, upload documents, or submit applications. Select **Ask before applying** to place a job in the checklist, then review the official announcement yourself. The only action link is to the USAJOBS announcement, where you can submit after your final review.

For a public deployment, protect the app with host authentication or another access-control layer. A shared USAJOBS API credential should not be exposed to an unauthenticated public audience.
