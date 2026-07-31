"""ERP 产品素材接口。

将外部 ERP 的产品查询限制在后端，并把用户最终选择的报价图复制入本地素材库。
这样文章生成和微信发布都依赖本地可控素材，而不是直接依赖 ERP 的图片 URL。
"""

import hashlib
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import String, cast
from sqlalchemy.orm import Session

from app.database import get_mysql_db
from app.deps import CurrentPrincipal, require_auth
from app.models.mysql_models import Asset
from app.services.asset_archive_service import build_archive_filename, save_image_to_asset_library
from app.services.erp_product_service import (
    ErpProductApiError,
    ErpProductConfigurationError,
    ErpProductSourceNotFoundError,
    build_erp_product_client_from_settings,
)
from app.services.storage_service import storage_service

router = APIRouter()


class ErpProductSourceResponse(BaseModel):
    """可公开给前端的品牌来源信息，不含任何 ERP 凭证。"""

    key: str
    name: str


class ErpProductSearchRequest(BaseModel):
    """公开 ERP 产品搜索允许的筛选字段，与对方接口文档保持一致。"""

    page_no: int = Field(1, ge=1, alias="pageNo")
    page_size: int = Field(10, ge=1, le=50, alias="pageSize")
    series: Optional[str] = None
    style: Optional[str] = None
    commodity_category: Optional[str] = Field(None, alias="commodityCategory")
    furniture_category: Optional[str] = Field(None, alias="furnitureCategory")
    price_min: Optional[float] = Field(None, ge=0, alias="priceMin")
    price_max: Optional[float] = Field(None, ge=0, alias="priceMax")
    product_model: Optional[str] = Field(None, alias="productModel")

    model_config = {"populate_by_name": True}


class ErpProductResponse(BaseModel):
    """文章选图页面显示的已规范化产品。"""

    name: str
    image_url: str
    series: List[str]
    style: str
    categories: List[str]
    tags: List[str]


class ErpProductSearchResponse(BaseModel):
    """产品查询分页结果。"""

    items: List[ErpProductResponse]
    total: int
    page_no: int
    page_size: int


class ImportErpProductImageRequest(BaseModel):
    """用户从 ERP 查询结果中选择一张报价图后提交的导入请求。"""

    image_url: str = Field(min_length=1, max_length=2048)
    product_name: str = Field(min_length=1, max_length=255)
    tags: List[str] = Field(default_factory=list, max_length=20)


class ImportedErpProductImageResponse(BaseModel):
    """导入本地素材库后的可预选图片地址。"""

    asset_id: int
    preview_url: str
    reused: bool = False


class ImportErpProductImagesRequest(BaseModel):
    """批量导入请求。

    上限固定在 20 张，避免一次操作占满对象存储、数据库连接和后台下载带宽。
    """

    products: List[ImportErpProductImageRequest] = Field(min_length=1, max_length=20)


class ImportErpProductImagesResponse(BaseModel):
    """批量导入结果，单张失败不影响其他已选择的产品图。"""

    items: List[ImportedErpProductImageResponse]
    imported_count: int
    reused_count: int
    failed_count: int
    errors: List[str]


def _translate_erp_error(exc: Exception) -> HTTPException:
    """将 ERP 服务错误翻译为不含密钥和上游响应体的 API 错误。"""
    if isinstance(exc, ErpProductSourceNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, ErpProductConfigurationError):
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


def _erp_image_marker(image_url: str) -> str:
    """为远端图片生成稳定标识，用于防止重复导入导致素材库无限增长。"""
    digest = hashlib.sha256(image_url.encode("utf-8")).hexdigest()[:24]
    return f"erp-image:{digest}"


async def _import_erp_product_image_to_library(
    db: Session,
    tenant_id: int,
    source_key: str,
    req: ImportErpProductImageRequest,
) -> ImportedErpProductImageResponse:
    """导入单张 ERP 图片，已导入时直接复用同租户现有素材。"""
    source = build_erp_product_client_from_settings().get_source(source_key)
    marker = _erp_image_marker(req.image_url)
    existing = (
        db.query(Asset)
        .filter(
            Asset.tenant_id == tenant_id,
            cast(Asset.tags, String).like(f"%{marker}%"),
        )
        .first()
    )
    if existing:
        # 历史导入记录使用 auto_archive 时间戳作为显示名。重复导入不新增素材，
        # 但应利用这次查询到的产品名回填显示字段，方便已有素材直接修复。
        current_filename = existing.original_filename or existing.filename
        if (
            req.product_name != "未命名产品"
            and current_filename.startswith("auto_archive_")
        ):
            extension = current_filename.rsplit(".", 1)[-1] if "." in current_filename else "jpg"
            existing.original_filename = build_archive_filename(req.product_name, extension)
            db.commit()
        return ImportedErpProductImageResponse(
            asset_id=existing.id,
            preview_url=storage_service.get_url(existing.storage_key),
            reused=True,
        )

    tag_text = ",".join(dict.fromkeys(["ERP产品", source.name, req.product_name, marker, *req.tags]))
    asset = await save_image_to_asset_library(
        db=db,
        tenant_id=tenant_id,
        image_url=req.image_url,
        keywords=tag_text,
        usage_type="erp_product_image",
        original_filename=req.product_name,
    )
    if not asset:
        raise ErpProductApiError("ERP 产品图片下载或入库失败")
    return ImportedErpProductImageResponse(
        asset_id=asset.id,
        preview_url=storage_service.get_url(asset.storage_key),
        reused=False,
    )


@router.get("/erp-product-sources", response_model=List[ErpProductSourceResponse])
def list_erp_product_sources(principal: CurrentPrincipal = Depends(require_auth)):
    """列出当前服务已配置的 ERP 品牌来源。"""
    del principal  # 认证用于租户边界；来源配置本身不向未认证调用方暴露。
    try:
        client = build_erp_product_client_from_settings()
        return [ErpProductSourceResponse(key=source.key, name=source.name) for source in client.list_sources()]
    except (ErpProductConfigurationError, ErpProductApiError) as exc:
        raise _translate_erp_error(exc)


@router.post("/erp-product-sources/{source_key}/products/search", response_model=ErpProductSearchResponse)
async def search_erp_products(
    source_key: str,
    req: ErpProductSearchRequest,
    principal: CurrentPrincipal = Depends(require_auth),
):
    """按服务端来源配置搜索 ERP 产品，凭证不会进入浏览器。"""
    del principal
    try:
        page = await build_erp_product_client_from_settings().search_products(
            source_key,
            req.model_dump(by_alias=True, exclude_none=True),
        )
        return ErpProductSearchResponse(
            items=[ErpProductResponse(
                name=product.name,
                image_url=product.image_url,
                series=product.series,
                style=product.style,
                categories=product.categories,
                tags=product.tags,
            ) for product in page.products],
            total=page.total,
            page_no=page.page_no,
            page_size=page.page_size,
        )
    except (ErpProductConfigurationError, ErpProductSourceNotFoundError, ErpProductApiError) as exc:
        raise _translate_erp_error(exc)


@router.post(
    "/erp-product-sources/{source_key}/images/import",
    response_model=ImportedErpProductImageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def import_erp_product_image(
    source_key: str,
    req: ImportErpProductImageRequest,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """下载报价图到本地素材库，再返回本地预览 URL 供文章使用。"""
    try:
        return await _import_erp_product_image_to_library(db, principal.tenant_id, source_key, req)
    except HTTPException:
        raise
    except (ErpProductConfigurationError, ErpProductSourceNotFoundError, ErpProductApiError) as exc:
        raise _translate_erp_error(exc)


@router.post(
    "/erp-product-sources/{source_key}/images/import-batch",
    response_model=ImportErpProductImagesResponse,
    status_code=status.HTTP_201_CREATED,
)
async def import_erp_product_images(
    source_key: str,
    req: ImportErpProductImagesRequest,
    db: Session = Depends(get_mysql_db),
    principal: CurrentPrincipal = Depends(require_auth),
):
    """批量导入用户勾选的 ERP 产品图，最多 20 张并对重复图片去重。"""
    items: List[ImportedErpProductImageResponse] = []
    errors: List[str] = []
    try:
        # 先校验来源，避免批量循环中对未知来源重复报错。
        build_erp_product_client_from_settings().get_source(source_key)
        for product in req.products:
            try:
                items.append(await _import_erp_product_image_to_library(
                    db, principal.tenant_id, source_key, product,
                ))
            except (ErpProductApiError, ValueError) as exc:
                errors.append(f"{product.product_name}：{exc}")
        reused_count = sum(1 for item in items if item.reused)
        return ImportErpProductImagesResponse(
            items=items,
            imported_count=len(items) - reused_count,
            reused_count=reused_count,
            failed_count=len(errors),
            errors=errors,
        )
    except (ErpProductConfigurationError, ErpProductSourceNotFoundError, ErpProductApiError) as exc:
        raise _translate_erp_error(exc)
