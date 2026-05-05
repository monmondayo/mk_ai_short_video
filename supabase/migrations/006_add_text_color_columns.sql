-- Add independent color settings for upper text and subtitle text.
alter table public.jobs
  add column if not exists upper_text_color text not null default '#000000',
  add column if not exists upper_edge_color text not null default '#FFDC00',
  add column if not exists subtitle_text_color text not null default '#FFFFFF',
  add column if not exists subtitle_edge_color text not null default '#FF1493';
