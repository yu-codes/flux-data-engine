# flux-data-engine frontend (Vite dev server; the image is used for local work)
FROM node:22-alpine

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund

COPY frontend .

EXPOSE 3001

CMD ["npm", "run", "dev", "--", "--host", "--port", "3001"]
