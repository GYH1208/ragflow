import { ButtonLoading } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { zodResolver } from '@hookform/resolvers/zod';
import { useEffect } from 'react';
import { useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { z } from 'zod';

const FormId = 'dataset-category-form';

type DatasetCategoryDialogProps = {
  open: boolean;
  title: string;
  initialName?: string;
  loading?: boolean;
  onOpenChange: (open: boolean) => void;
  onSubmit: (name: string) => Promise<unknown>;
};

export function DatasetCategoryDialog({
  open,
  title,
  initialName = '',
  loading = false,
  onOpenChange,
  onSubmit,
}: DatasetCategoryDialogProps) {
  const { t } = useTranslation();
  const schema = z.object({
    name: z
      .string()
      .trim()
      .min(1, t('knowledgeList.categoryNameRequired'))
      .max(128, t('knowledgeList.categoryNameTooLong')),
  });
  const form = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema),
    defaultValues: { name: initialName },
  });

  useEffect(() => {
    form.reset({ name: initialName });
  }, [form, initialName, open]);

  const handleSubmit = async ({ name }: z.infer<typeof schema>) => {
    try {
      await onSubmit(name.trim());
      onOpenChange(false);
    } catch {
      // The request hook displays the server-side validation error. Keep the
      // dialog open so the user can fix duplicate or invalid names.
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[425px]">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        <Form {...form}>
          <form
            id={FormId}
            className="space-y-5"
            onSubmit={form.handleSubmit(handleSubmit)}
          >
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>{t('knowledgeList.categoryName')}</FormLabel>
                  <FormControl>
                    <Input
                      autoFocus
                      placeholder={t('knowledgeList.categoryNamePlaceholder')}
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
          </form>
        </Form>
        <DialogFooter>
          <ButtonLoading type="submit" form={FormId} loading={loading}>
            {t('common.save')}
          </ButtonLoading>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
