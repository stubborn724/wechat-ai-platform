import client from './client'

/** 后端可公开的 ERP 产品来源，不包含任何上游系统密钥。 */
export interface ErpProductSource {
  key: string
  name: string
}

/** ERP 产品查询结果已由后端规整，前端只负责展示与选择。 */
export interface ErpProduct {
  name: string
  image_url: string
  series: string[]
  style: string
  categories: string[]
  tags: string[]
}

export interface ErpProductPage {
  items: ErpProduct[]
  total: number
  page_no: number
  page_size: number
}

/** 获取当前后端已配置、允许调用的品牌来源。 */
export async function listErpProductSources(): Promise<ErpProductSource[]> {
  const res = await client.get('/erp-product-sources')
  return res.data.data || res.data || []
}

/** 根据型号、系列或品类筛选 ERP 产品。 */
export async function searchErpProducts(
  sourceKey: string,
  params: { pageNo?: number; pageSize?: number; productModel?: string; series?: string; commodityCategory?: string },
): Promise<ErpProductPage> {
  const res = await client.post(`/erp-product-sources/${encodeURIComponent(sourceKey)}/products/search`, params)
  return res.data.data || res.data
}

/** 将用户选中的 ERP 报价图复制到本地素材库，返回可直接用于文章的本地 URL。 */
export async function importErpProductImage(
  sourceKey: string,
  product: ErpProduct,
): Promise<{ asset_id: number; preview_url: string }> {
  const res = await client.post(`/erp-product-sources/${encodeURIComponent(sourceKey)}/images/import`, {
    image_url: product.image_url,
    product_name: product.name,
    tags: product.tags,
  })
  return res.data.data || res.data
}

/** 批量导入勾选产品图。后端强制限制为 20 张并对重复远端图片复用本地素材。 */
export async function importErpProductImages(
  sourceKey: string,
  products: ErpProduct[],
): Promise<{ imported_count: number; reused_count: number; failed_count: number; errors: string[] }> {
  const res = await client.post(`/erp-product-sources/${encodeURIComponent(sourceKey)}/images/import-batch`, {
    products: products.map((product) => ({
      image_url: product.image_url,
      product_name: product.name,
      tags: product.tags,
    })),
  })
  return res.data.data || res.data
}
