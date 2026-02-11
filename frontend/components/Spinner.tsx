import { FC } from 'react';

type SpinnerProps = {
    size?: 'sm' | 'md' | 'lg';
    className?: string;
    light?: boolean;
};

const Spinner: FC<SpinnerProps> = ({ size = 'md', className = '', light = false }) => {
    const sizeClasses = {
        sm: 'w-4 h-4 border-2',
        md: 'w-6 h-6 border-2',
        lg: 'w-8 h-8 border-3',
    };

    const colorClass = light ? 'border-t-white border-white/30' : 'border-t-primary-600 border-primary-200';

    return (
        <div
            className={`rounded-full animate-spin ${sizeClasses[size]} ${colorClass} ${className}`}
            role="status"
        >
            <span className="sr-only">Loading...</span>
        </div>
    );
};

export default Spinner;
