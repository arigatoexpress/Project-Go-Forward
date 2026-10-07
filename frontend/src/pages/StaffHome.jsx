import React from 'react';
import { Camera, ClipboardList, FileText, Users } from 'lucide-react';

const TASKS = [
  { key: 'photos', label: 'Add Photos', hint: 'Put pictures on a home for sale', icon: Camera },
  { key: 'manage-inventory', label: 'Manage Homes', hint: 'Add, edit, or take a home off the website', icon: ClipboardList },
  { key: 'documents', label: 'Documents', hint: 'Fill and share paperwork', icon: FileText },
  { key: 'crm', label: 'Leads', hint: 'See who asked about a home', icon: Users },
];

export default function StaffHome({ onNavigate }) {
  return (
    <div className="max-w-3xl mx-auto px-4 py-8 text-[var(--cp-text)]">
      <h1 className="text-3xl font-bold mb-2">Staff Home</h1>
      <p className="text-lg text-[var(--cp-muted)] mb-8 leading-relaxed">
        Pick a job. Everything else can wait.
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {TASKS.map((task) => {
          const Icon = task.icon;
          return (
            <button
              key={task.key}
              type="button"
              onClick={() => onNavigate(task.key)}
              className="flex items-start gap-4 rounded-2xl border border-[var(--cp-border)] bg-[var(--cp-panel)] p-5 text-left shadow-sm hover:border-[var(--cp-accent)] hover:bg-[var(--cp-surface)] min-h-[7rem]"
            >
              <span className="rounded-xl bg-[var(--cp-accent-dim)] p-3 text-[var(--cp-accent)]">
                <Icon size={28} aria-hidden="true" />
              </span>
              <span>
                <span className="block text-xl font-semibold">{task.label}</span>
                <span className="block mt-1 text-sm text-[var(--cp-muted)] leading-relaxed">{task.hint}</span>
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
