import { DataFlowSelect } from '@/components/data-pipeline-select';
import { ButtonLoading } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
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
import { RAGFlowSelect } from '@/components/ui/select';
import { FormLayout } from '@/constants/form';
import { ParseType } from '@/constants/knowledge';
import { useFetchDefaultModelDictionary } from '@/hooks/use-llm-request';
import { IModalProps } from '@/interfaces/common';
import { IDatasetCategory } from '@/interfaces/database/dataset';
import { zodResolver } from '@hookform/resolvers/zod';
import { omit } from 'lodash';
import { useEffect } from 'react';
import { useForm, useWatch } from 'react-hook-form';
import { useTranslation } from 'react-i18next';
import { z } from 'zod';
import {
  ChunkMethodItem,
  EmbeddingModelItem,
  ParseTypeItem,
} from '../dataset/dataset-setting/configuration/common-item';
import { UNCATEGORIZED_CATEGORY } from './category-constants';

const FormId = 'dataset-creating-form';

const ChunkMethodName = 'chunk_method';

type DatasetCreatingCategoryProps = {
  categories?: IDatasetCategory[];
  defaultCategoryId?: string | null;
};

export function InputForm({
  onOk,
  categories = [],
  defaultCategoryId,
}: IModalProps<any> & DatasetCreatingCategoryProps) {
  const { t } = useTranslation();
  const defaultModelDictionary = useFetchDefaultModelDictionary();

  const FormSchema = z
    .object({
      name: z
        .string()
        .min(1, {
          message: t('knowledgeList.namePlaceholder'),
        })
        .trim(),
      category_id: z.string(),
      parseType: z.nativeEnum(ParseType).optional(),
      embedding_model: z
        .string()
        .min(1, {
          message: t('knowledgeConfiguration.embeddingModelPlaceholder'),
        })
        .trim(),
      [ChunkMethodName]: z.string().optional(),
      pipeline_id: z.string().optional(),
    })
    .superRefine((data, ctx) => {
      // When parseType === BuiltIn, chunk_method is required
      if (
        data.parseType === ParseType.BuiltIn &&
        (!data[ChunkMethodName] || data[ChunkMethodName].trim() === '')
      ) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: t('knowledgeList.parserRequired'),
          path: [ChunkMethodName],
        });
      }
      // When parseType === Pipeline, pipeline_id required
      if (data.parseType === ParseType.Pipeline && !data.pipeline_id) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: t('knowledgeList.dataFlowRequired'),
          path: ['pipeline_id'],
        });
      }
    });

  const form = useForm<z.infer<typeof FormSchema>>({
    resolver: zodResolver(FormSchema),
    defaultValues: {
      name: '',
      category_id: defaultCategoryId || UNCATEGORIZED_CATEGORY,
      parseType: ParseType.BuiltIn,
      [ChunkMethodName]: '',
      embedding_model: defaultModelDictionary?.embd_id,
    },
  });

  const parseType = useWatch({
    control: form.control,
    name: 'parseType',
  });

  function onSubmit(data: z.infer<typeof FormSchema>) {
    const normalizedData = {
      ...data,
      category_id:
        data.category_id === UNCATEGORIZED_CATEGORY ? null : data.category_id,
    };
    const nextNormalizedData =
      parseType === ParseType.BuiltIn
        ? normalizedData
        : omit(normalizedData, ChunkMethodName);
    onOk?.(nextNormalizedData);
  }

  useEffect(() => {
    if (parseType === ParseType.BuiltIn) {
      form.setValue('pipeline_id', '');
    }
    if (defaultModelDictionary?.embd_id) {
      form.setValue('embedding_model', defaultModelDictionary?.embd_id);
    }
  }, [parseType, form, defaultModelDictionary]);

  return (
    <Form {...form}>
      <form
        onSubmit={form.handleSubmit(onSubmit, (errors) => {
          console.warn(errors);
        })}
        className="space-y-6"
        id={FormId}
      >
        <FormField
          control={form.control}
          name="name"
          render={({ field }) => (
            <FormItem>
              <FormLabel>
                <span className="text-destructive mr-1"> *</span>
                {t('knowledgeList.name')}
              </FormLabel>
              <FormControl>
                <Input
                  placeholder={t('knowledgeList.namePlaceholder')}
                  {...field}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="category_id"
          render={({ field }) => (
            <FormItem>
              <FormLabel>{t('knowledgeList.category')}</FormLabel>
              <FormControl>
                <RAGFlowSelect
                  {...field}
                  options={[
                    {
                      value: UNCATEGORIZED_CATEGORY,
                      label: t('knowledgeList.uncategorized'),
                    },
                    ...categories.map((category) => ({
                      value: category.id,
                      label: category.name,
                    })),
                  ]}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />

        <EmbeddingModelItem line={2} isEdit={false} />
        <ParseTypeItem />
        {parseType === ParseType.BuiltIn && (
          <ChunkMethodItem name={ChunkMethodName}></ChunkMethodItem>
        )}
        {parseType === ParseType.Pipeline && (
          <DataFlowSelect
            isMult={false}
            showToDataPipeline={true}
            formFieldName="pipeline_id"
            layout={FormLayout.Vertical}
          />
        )}
      </form>
    </Form>
  );
}

export function DatasetCreatingDialog({
  hideModal,
  onOk,
  loading,
  categories,
  defaultCategoryId,
}: IModalProps<any> & DatasetCreatingCategoryProps) {
  const { t } = useTranslation();

  return (
    <Dialog open onOpenChange={hideModal}>
      <DialogContent
        className="sm:max-w-[425px] focus-visible:!outline-none flex flex-col"
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            const form = document.getElementById(FormId) as HTMLFormElement;
            form?.requestSubmit();
          }
        }}
      >
        <DialogHeader>
          <DialogTitle>{t('knowledgeList.createKnowledgeBase')}</DialogTitle>
        </DialogHeader>
        <DialogDescription></DialogDescription>
        <InputForm
          onOk={onOk}
          categories={categories}
          defaultCategoryId={defaultCategoryId}
        ></InputForm>
        <DialogFooter>
          <ButtonLoading type="submit" form={FormId} loading={loading}>
            {t('common.save')}
          </ButtonLoading>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
