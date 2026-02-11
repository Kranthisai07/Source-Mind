import { FC } from 'react';

type SkeletonProps = {
    className?: string;
    variant?: 'text' | 'circular' | 'rectangular';
    width?: string | number;
    height?: string | number;
};

const Skeleton: FC<SkeletonProps> = ({
    className = '',
    variant = 'text',
    width,
    height
}) => {
    const baseClasses = "animate-pulse bg-neutral-200 dark:bg-neutral-700/50";

    const variantClasses = {
        text: "rounded h-4",
        circular: "rounded-full",
        rectangular: "rounded-md",
    };

    const style = {
        width: width,
        height: height,
    };

    return (
        <div
            className={`${baseClasses} ${variantClasses[variant]} ${className}`}
            style={style}
        />
    );
};

export default Skeleton;
