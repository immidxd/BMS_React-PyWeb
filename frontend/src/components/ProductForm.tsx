import React, { useState, useEffect } from 'react';
import {
    Dialog,
    DialogTitle,
    DialogContent,
    DialogActions,
    TextField,
    Button,
    FormControl,
    InputLabel,
    Select,
    MenuItem,
    Box
} from '@mui/material';
import Grid from '@mui/material/Grid';
import { Product, ReferenceItem } from '../types/product';
import { productService } from '../services/productService';

interface ProductFormProps {
    open: boolean;
    onClose: () => void;
    onSubmit: (product: Partial<Product>) => Promise<void>;
    initialData?: Product;
}

export const ProductForm: React.FC<ProductFormProps> = ({
    open,
    onClose,
    onSubmit,
    initialData
}) => {
    const [formData, setFormData] = useState<Partial<Product>>(initialData || {});
    const [filters, setFilters] = useState<{
        brands: ReferenceItem[];
        types: ReferenceItem[];
        subtypes: ReferenceItem[];
        colors: ReferenceItem[];
        countries: ReferenceItem[];
        statuses: ReferenceItem[];
        conditions: ReferenceItem[];
        genders: ReferenceItem[];
    }>({
        brands: [],
        types: [],
        subtypes: [],
        colors: [],
        countries: [],
        statuses: [],
        conditions: [],
        genders: []
    });
    const [isSubmitting, setIsSubmitting] = useState(false);

    useEffect(() => {
        setFormData(initialData || {});
    }, [initialData]);

    useEffect(() => {
        if (open) {
            loadFilters();
        }
    }, [open]);

    const loadFilters = async () => {
        try {
            const response = await productService.getFilters();
            setFilters({
                brands: response.brands,
                types: response.types,
                subtypes: response.subtypes,
                colors: response.colors,
                countries: response.countries,
                statuses: response.statuses,
                conditions: response.conditions,
                genders: response.genders
            });
        } catch (error) {
            console.error('Error loading filters:', error);
        }
    };

    const handleChange = (field: keyof Product, value: any) => {
        setFormData(prev => ({
            ...prev,
            [field]: value
        }));
    };

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setIsSubmitting(true);
        try {
            await onSubmit(formData);
            onClose();
        } catch (err) {
            // eslint-disable-next-line no-console
            console.error('Submit error:', err);
        } finally {
            setIsSubmitting(false);
        }
    };

    return (
        <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
            <DialogTitle>
                {initialData ? 'Edit Product' : 'Create Product'}
            </DialogTitle>
            <form onSubmit={handleSubmit}>
                <DialogContent>
                    <Grid container spacing={2}>
                        <Grid style={{ width: '50%' }}>
                            <TextField
                                fullWidth
                                label="Product Number"
                                value={formData.productnumber || ''}
                                onChange={(e) => handleChange('productnumber', e.target.value)}
                                required
                            />
                        </Grid>
                        <Grid style={{ width: '50%' }}>
                            <TextField
                                fullWidth
                                label="Model"
                                value={formData.model || ''}
                                onChange={(e) => handleChange('model', e.target.value)}
                            />
                        </Grid>
                        <Grid style={{ width: '50%' }}>
                            <FormControl fullWidth>
                                <InputLabel>Brand</InputLabel>
                                <Select
                                    value={formData.brandid ?? ''}
                                    onChange={(e) => handleChange('brandid', e.target.value)}
                                    label="Brand"
                                >
                                    {filters.brands.map(brand => (
                                        <MenuItem key={brand.id} value={brand.id}>
                                            {brand.name}
                                        </MenuItem>
                                    ))}
                                </Select>
                            </FormControl>
                        </Grid>
                        <Grid style={{ width: '50%' }}>
                            <FormControl fullWidth>
                                <InputLabel>Type</InputLabel>
                                <Select
                                    value={formData.typeid ?? ''}
                                    onChange={(e) => handleChange('typeid', e.target.value)}
                                    label="Type"
                                >
                                    {filters.types.map(type => (
                                        <MenuItem key={type.id} value={type.id}>
                                            {type.name}
                                        </MenuItem>
                                    ))}
                                </Select>
                            </FormControl>
                        </Grid>
                        <Grid style={{ width: '50%' }}>
                            <TextField
                                fullWidth
                                label="Price"
                                type="number"
                                value={formData.price ?? ''}
                                onChange={(e) => handleChange('price', e.target.value === '' ? undefined : Number(e.target.value))}
                            />
                        </Grid>
                        <Grid style={{ width: '50%' }}>
                            <TextField
                                fullWidth
                                label="Quantity"
                                type="number"
                                value={formData.quantity ?? ''}
                                onChange={(e) => handleChange('quantity', e.target.value === '' ? undefined : Number(e.target.value))}
                                required
                            />
                        </Grid>
                        <Grid style={{ width: '50%' }}>
                            <FormControl fullWidth>
                                <InputLabel>Status</InputLabel>
                                <Select
                                    value={formData.statusid ?? ''}
                                    onChange={(e) => handleChange('statusid', e.target.value)}
                                    label="Status"
                                >
                                    {filters.statuses.map(status => (
                                        <MenuItem key={status.id} value={status.id}>
                                            {status.name}
                                        </MenuItem>
                                    ))}
                                </Select>
                            </FormControl>
                        </Grid>
                        <Grid style={{ width: '50%' }}>
                            <FormControl fullWidth>
                                <InputLabel>Condition</InputLabel>
                                <Select
                                    value={formData.conditionid ?? ''}
                                    onChange={(e) => handleChange('conditionid', e.target.value)}
                                    label="Condition"
                                >
                                    {filters.conditions.map(condition => (
                                        <MenuItem key={condition.id} value={condition.id}>
                                            {condition.name}
                                        </MenuItem>
                                    ))}
                                </Select>
                            </FormControl>
                        </Grid>
                        <Grid style={{ width: '100%' }}>
                            <TextField
                                fullWidth
                                label="Description"
                                multiline
                                rows={4}
                                value={formData.description || ''}
                                onChange={(e) => handleChange('description', e.target.value)}
                            />
                        </Grid>
                    </Grid>
                </DialogContent>
                <DialogActions>
                    <Button onClick={onClose} disabled={isSubmitting}>Cancel</Button>
                    <Button type="submit" variant="contained" color="primary" disabled={isSubmitting}>
                        {initialData ? 'Update' : 'Create'}
                    </Button>
                </DialogActions>
            </form>
        </Dialog>
    );
}; 