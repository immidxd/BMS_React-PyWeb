export interface ReferenceItem {
    id: number;
    name: string;
}

export interface Product {
    id: number;
    productnumber: string;
    clonednumbers?: string;
    model?: string;
    marking?: string;
    year?: number;
    description?: string;
    extranote?: string;
    price?: number;
    oldprice?: number;
    dateadded: string;
    sizeeu?: string;
    sizeua?: string;
    sizeusa?: string;
    sizeuk?: string;
    sizejp?: string;
    sizecn?: string;
    measurementscm?: string;
    quantity: number;
    typeid?: number;
    subtypeid?: number;
    brandid?: number;
    genderid?: number;
    colorid?: number;
    ownercountryid?: number;
    manufacturercountryid?: number;
    statusid?: number;
    conditionid?: number;
    importid?: number;
    deliveryid?: number;
    mainimage?: string;
    is_visible: boolean;
    created_at: string;
    updated_at: string;
    
    // Related data (ORM objects)
    type?: ReferenceItem;
    subtype?: ReferenceItem;
    brand?: ReferenceItem;
    gender?: ReferenceItem;
    color?: ReferenceItem;
    owner_country?: ReferenceItem;
    manufacturer_country?: ReferenceItem;
    status?: ReferenceItem;
    condition?: ReferenceItem;
    import_record?: ReferenceItem;
    delivery?: ReferenceItem;

    // Flat joined name fields returned by SQL JOIN in product_service
    type_name?: string;
    brand_name?: string;
    status_name?: string;
    color_name?: string;
    condition_name?: string;
    gender_name?: string;
    subtype_name?: string;
    supplier_name?: string;

    // Computed from order_items
    sold_count?: number;
    available_qty?: number;
    pnum_dup_brands?: number;

    // Rostovka detection: quantity>1, OR (n) suffix variant, OR has (n) child
    is_rostovka?: boolean;
}

export interface ProductListResponse {
    items: Product[];
    total: number;
    page: number;
    per_page: number;
    pages: number;
}

export interface ProductFilters {
    brands: ReferenceItem[];
    types: ReferenceItem[];
    subtypes: ReferenceItem[];
    colors: ReferenceItem[];
    countries: ReferenceItem[];
    statuses: ReferenceItem[];
    conditions: ReferenceItem[];
    genders: ReferenceItem[];
    shipments: { id: number; name: string; date: string | null }[];
    price_range: {
        min_price: number;
        max_price: number;
    };
    size_ranges: {
        eu: string[];
        ua: string[];
        usa: string[];
        uk: string[];
        jp: string[];
        cn: string[];
    };
}

export interface ProductFilter {
    search?: string;
    // Legacy single-id
    typeid?: number;
    subtypeid?: number;
    brandid?: number;
    genderid?: number;
    colorid?: number;
    statusid?: number;
    conditionid?: number;
    // Multi-id arrays (used by filter panel)
    typeids?: number[];
    subtypeids?: number[];
    brandids?: number[];
    genderids?: number[];
    colorids?: number[];
    statusids?: number[];
    conditionids?: number[];
    // Price range
    min_price?: number;
    max_price?: number;
    // Size (multi-select)
    sizeeu?: string[];
    // Stock / visibility
    with_stock_only?: boolean;
    is_visible?: boolean;
    // Legacy aliases kept for compatibility
    brands?: number[];
    types?: number[];
    colors?: number[];
    countries?: number[];
    only_unsold?: boolean;
    visible_only?: boolean;
    shipment_id?: number;
}