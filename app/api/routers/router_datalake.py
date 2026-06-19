from fastapi import APIRouter, Depends, HTTPException
import boto3
from botocore.exceptions import ClientError
from app.api.routers.router_auth import get_current_user

router = APIRouter(prefix="/api/v1/datalake", tags=["Data Lake AWS"])

@router.get("/files")
async def listar_arquivos_parquet(usuario_logado: dict = Depends(get_current_user)):
    """Varre a camada Bronze no S3 e expõe a auditoria dos arquivos .parquet."""
    s3_client = boto3.client('s3')
    bucket_name = "nexus-datalake-linea-prd"
    prefix = "mtrix/"
    
    try:
        response = s3_client.list_objects_v2(Bucket=bucket_name, Prefix=prefix)
        arquivos = []
        
        for obj in response.get('Contents', []):
            if obj['Key'].endswith('.parquet'):
                arquivos.append({
                    "nome": obj['Key'].replace(prefix, ''),
                    "tamanho_mb": round(obj['Size'] / (1024 * 1024), 2),
                    "ultima_atualizacao": obj['LastModified'].strftime("%d/%m/%Y %H:%M:%S")
                })
        return arquivos
    except ClientError as ce:
        print(f"Erro AWS S3: {ce}")
        return {"erro": "Falha na comunicação com o Amazon S3", "arquivos": []}
    except Exception as e:
        return {"erro": str(e), "arquivos": []}