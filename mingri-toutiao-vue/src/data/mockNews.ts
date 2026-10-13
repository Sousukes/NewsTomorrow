import type { NewsItem } from '@/types';

/**
 * 模拟 GET /news 返回的数据。
 * 真实接入时由接口填充 cover / images 远程 URL，这里用占位渐变呈现。
 */
export const mockNews: NewsItem[] = [
  {
    id: 'n1',
    title: '国产大飞机再添新航线，商业运营迈出关键一步',
    summary:
      '今日，国产大型客机在多条新航线上正式投入商业运营，标志着我国民用航空制造能力迈上新台阶。',
    source: '新华社',
    commentCount: 320,
    time: '2小时前',
    images: ['', '', ''],
    channel: 'tech',
    variant: 'triple',
  },
  {
    id: 'n2',
    title: '多地出台楼市新政，刚需购房门槛再降低',
    summary: '近期多个城市优化住房信贷政策，首套房贷利率与首付比例同步下调，刚需家庭受益明显。',
    source: '财经周刊',
    commentCount: 156,
    time: '3小时前',
    cover: '',
    channel: 'finance',
    variant: 'left-right',
  },
  {
    id: 'n3',
    title: '中央气象台发布暴雨蓝色预警，这些地区需注意防范',
    summary: '未来24小时，华南、江南南部等地将出现大到暴雨，局地伴有雷暴大风，请提前做好排涝准备。',
    source: '中国天气',
    commentCount: 89,
    time: '1小时前',
    channel: 'recommend',
    isTop: true,
    variant: 'text',
  },
  {
    id: 'n4',
    title: '现场直击：国产航母编队展开远海实战化训练',
    summary: '编队连续航行数昼夜，完成舰载机起降、编队机动等多课目训练，体系作战能力得到检验。',
    source: '军事频道',
    playCount: 128000,
    time: '5小时前',
    cover: '',
    channel: 'video',
    variant: 'video',
  },
  {
    id: 'n5',
    title: '新能源汽车出口连续三月创新高，海外市场持续扩容',
    summary: '前五月新能源汽车出口同比大幅增长，欧洲与东南亚成为主要增量市场。',
    source: '新华财经',
    commentCount: 245,
    time: '4小时前',
    images: ['', '', ''],
    channel: 'tech',
    variant: 'triple',
  },
  {
    id: 'n6',
    title: '教育部：推动义务教育阶段课后服务全覆盖',
    summary: '各地统筹校内外资源，丰富科普、体育、艺术等课程，缓解家长接送与看护压力。',
    source: '人民日报',
    commentCount: 512,
    time: '6小时前',
    cover: '',
    channel: 'recommend',
    variant: 'left-right',
  },
];
