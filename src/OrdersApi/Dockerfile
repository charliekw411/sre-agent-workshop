# Build stage
FROM mcr.microsoft.com/dotnet/sdk:8.0 AS build
WORKDIR /src
COPY OrdersApi.csproj ./
RUN dotnet restore OrdersApi.csproj
COPY . ./
RUN dotnet publish OrdersApi.csproj --configuration Release --output /app/publish /p:UseAppHost=false

# Runtime stage
FROM mcr.microsoft.com/dotnet/aspnet:8.0 AS runtime
WORKDIR /app
COPY --from=build /app/publish ./

ENV ASPNETCORE_URLS=http://+:8080 \
    DOTNET_EnableDiagnostics=0

EXPOSE 8080

# Runs as the non-root user provided by the base image.
USER $APP_UID

ENTRYPOINT ["dotnet", "OrdersApi.dll"]
