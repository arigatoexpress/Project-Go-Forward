import React from 'react';
import { Home, Wrench, Phone } from 'lucide-react';
import { BUSINESS_PHONE, BUSINESS_PHONE_RAW } from '../constants';

const QuickActions = ({ onActionClick, disabled }) => {
    const actions = [
        {
            id: 'inventory',
            label: 'Find a Home',
            icon: Home,
            message: "I'd like to find a home.",
            color: 'bg-blue-600 hover:bg-blue-700',
        },
        {
            id: 'service',
            label: 'Service/Warranty',
            icon: Wrench,
            message: "I need help with a service or warranty issue.",
            color: 'bg-orange-600 hover:bg-orange-700',
        },
        {
            // Click-to-call is the highest-intent mobile conversion action for a
            // home dealership — render a real tel: link, not a chat message.
            id: 'call',
            label: `Call ${BUSINESS_PHONE}`,
            icon: Phone,
            href: `tel:${BUSINESS_PHONE_RAW}`,
            color: 'bg-green-600 hover:bg-green-700',
        },
    ];

    const className = (color) =>
        `flex items-center gap-2 px-4 py-2.5 rounded-full text-white text-sm font-medium
              ${color} transition-all duration-200 shadow-sm hover:shadow-md
              disabled:opacity-50 disabled:cursor-not-allowed transform hover:scale-105 active:scale-95`;

    return (
        <div className="flex flex-wrap gap-2 mt-3 mb-1">
            {actions.map((action) => {
                const Icon = action.icon;
                if (action.href) {
                    return (
                        <a key={action.id} href={action.href} className={className(action.color)}>
                            <Icon size={16} />
                            <span>{action.label}</span>
                        </a>
                    );
                }
                return (
                    <button
                        key={action.id}
                        onClick={() => onActionClick(action.message)}
                        disabled={disabled}
                        className={className(action.color)}
                    >
                        <Icon size={16} />
                        <span>{action.label}</span>
                    </button>
                );
            })}
        </div>
    );
};

export default QuickActions;
