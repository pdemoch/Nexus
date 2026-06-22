# Estágio 1: Build do React (Node.js)
FROM node:20-alpine as build
WORKDIR /app

# Copia os ficheiros de dependências e instala
COPY package*.json ./
RUN npm install

# Copia todo o código fonte
COPY . .

# Faz a compilação de produção (gera a pasta 'dist')
RUN npm run build

# Estágio 2: Servidor Web (Nginx)
FROM nginx:alpine

# Remove a página padrão do Nginx
RUN rm -rf /usr/share/nginx/html/*

# Copia os ficheiros compilados do estágio 1
COPY --from=build /app/dist /usr/share/nginx/html

# Copia a nossa configuração de rotas
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]