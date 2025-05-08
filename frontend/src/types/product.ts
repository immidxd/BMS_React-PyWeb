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
    
    // Related data
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
    typeid?: number;
    subtypeid?: number;
    brandid?: number;
    statusid?: number;
    min_price?: number;
    max_price?: number;
    brands?: number[];
    types?: number[];
    colors?: number[];
    countries?: number[];
    only_unsold?: boolean;
    visible_only?: boolean;
} 