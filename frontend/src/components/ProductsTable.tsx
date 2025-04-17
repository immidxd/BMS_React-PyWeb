import React, { useState, useEffect } from 'react';
import {
    Table,
    TableBody,
    TableCell,
    TableContainer,
    TableHead,
    TableRow,
    Paper,
    TablePagination,
    IconButton,
    Tooltip,
    Chip,
    Box,
    TextField,
    MenuItem,
    Grid
} from '@mui/material';
import {
    Edit as EditIcon,
    Delete as DeleteIcon,
    Visibility as VisibilityIcon,
    VisibilityOff as VisibilityOffIcon
} from '@mui/icons-material';
import { Product, ProductFilter } from '../types/product';
import { productService } from '../services/productService';

interface ProductsTableProps {
    onEdit: (product: Product) => void;
    onDelete: (id: number) => void;
    onToggleVisibility: (id: number) => void;
}

export const ProductsTable: React.FC<ProductsTableProps> = ({
    onEdit,
    onDelete,
    onToggleVisibility
}) => {
    const [products, setProducts] = useState<Product[]>([]);
    const [page, setPage] = useState(0);
    const [rowsPerPage, setRowsPerPage] = useState(10);
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(false);
    const [filters, setFilters] = useState<ProductFilter>({});

    const fetchProducts = async () => {
        try {
            setLoading(true);
            const response = await productService.getProducts(page + 1, filters);
            setProducts(response.items);
            setTotal(response.total);
        } catch (error) {
            console.error('Error fetching products:', error);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchProducts();
    }, [page, rowsPerPage, filters]);

    const handleChangePage = (event: unknown, newPage: number) => {
        setPage(newPage);
    };

    const handleChangeRowsPerPage = (event: React.ChangeEvent<HTMLInputElement>) => {
        setRowsPerPage(parseInt(event.target.value, 10));
        setPage(0);
    };

    const handleFilterChange = (field: keyof ProductFilter, value: any) => {
        setFilters(prev => ({
            ...prev,
            [field]: value
        }));
        setPage(0);
    };

    return (
        <Paper sx={{ width: '100%', overflow: 'hidden' }}>
            <Box sx={{ p: 2 }}>
                <Grid container spacing={2}>
                    <Grid item xs={12} sm={6} md={3}>
                        <TextField
                            fullWidth
                            label="Search"
                            value={filters.search || ''}
                            onChange={(e) => handleFilterChange('search', e.target.value)}
                        />
                    </Grid>
                    <Grid item xs={12} sm={6} md={3}>
                        <TextField
                            fullWidth
                            label="Min Price"
                            type="number"
                            value={filters.min_price || ''}
                            onChange={(e) => handleFilterChange('min_price', Number(e.target.value))}
                        />
                    </Grid>
                    <Grid item xs={12} sm={6} md={3}>
                        <TextField
                            fullWidth
                            label="Max Price"
                            type="number"
                            value={filters.max_price || ''}
                            onChange={(e) => handleFilterChange('max_price', Number(e.target.value))}
                        />
                    </Grid>
                </Grid>
            </Box>

            <TableContainer sx={{ maxHeight: 440 }}>
                <Table stickyHeader>
                    <TableHead>
                        <TableRow>
                            <TableCell>ID</TableCell>
                            <TableCell>Product Number</TableCell>
                            <TableCell>Model</TableCell>
                            <TableCell>Brand</TableCell>
                            <TableCell>Type</TableCell>
                            <TableCell>Price</TableCell>
                            <TableCell>Quantity</TableCell>
                            <TableCell>Status</TableCell>
                            <TableCell>Actions</TableCell>
                        </TableRow>
                    </TableHead>
                    <TableBody>
                        {products.map((product) => (
                            <TableRow hover key={product.id}>
                                <TableCell>{product.id}</TableCell>
                                <TableCell>{product.productnumber}</TableCell>
                                <TableCell>{product.model}</TableCell>
                                <TableCell>{product.brand?.name}</TableCell>
                                <TableCell>{product.type?.name}</TableCell>
                                <TableCell>{product.price}</TableCell>
                                <TableCell>{product.quantity}</TableCell>
                                <TableCell>
                                    <Chip
                                        label={product.status?.name || 'Unknown'}
                                        color={product.quantity > 0 ? 'success' : 'error'}
                                    />
                                </TableCell>
                                <TableCell>
                                    <Tooltip title="Edit">
                                        <IconButton onClick={() => onEdit(product)}>
                                            <EditIcon />
                                        </IconButton>
                                    </Tooltip>
                                    <Tooltip title="Delete">
                                        <IconButton onClick={() => onDelete(product.id)}>
                                            <DeleteIcon />
                                        </IconButton>
                                    </Tooltip>
                                    <Tooltip title={product.is_visible ? "Hide" : "Show"}>
                                        <IconButton onClick={() => onToggleVisibility(product.id)}>
                                            {product.is_visible ? <VisibilityIcon /> : <VisibilityOffIcon />}
                                        </IconButton>
                                    </Tooltip>
                                </TableCell>
                            </TableRow>
                        ))}
                    </TableBody>
                </Table>
            </TableContainer>
            <TablePagination
                rowsPerPageOptions={[10, 25, 100]}
                component="div"
                count={total}
                rowsPerPage={rowsPerPage}
                page={page}
                onPageChange={handleChangePage}
                onRowsPerPageChange={handleChangeRowsPerPage}
            />
        </Paper>
    );
}; 