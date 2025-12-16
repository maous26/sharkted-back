# Utilisation de l'image officielle Playwright (Python inclus)
# Cette image contient déjà Python, Playwright et les navigateurs requis.
FROM mcr.microsoft.com/playwright/python:v1.42.0-jammy

WORKDIR /app

# Installation des dépendances Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copie intégrale du code source
COPY . .

# Variables d'environnement
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Port
EXPOSE 3000

# Commande par défaut pour l'API (pointe vers main.py)
# Les workers surchargeront cette commande via docker-compose ou Railway
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "3000", "--proxy-headers"]
